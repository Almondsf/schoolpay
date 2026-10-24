"""
A read-only check of a live deployment. It changes nothing, so it's safe to run against production
after every deploy:

    python -m app.smoke https://wardpay.netlify.app
    python -m app.smoke https://wardpay.netlify.app --api https://schoolpay-a97t.onrender.com

It checks that the web app and the API answer (the first request may wake a sleeping free-plan service,
so it waits up to two minutes), that the database and Redis are reachable, that development-only routes
and the API docs are switched off, that sign-in is needed where it should be, that forged webhooks and
made-up receipt codes are refused, and that the security headers and HTTPS redirect are in place.
Prints one line per check and exits 1 if any failed.
"""

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass

import httpx

FIRST_WAIT = 120.0  # seconds: a sleeping free-plan service takes about 90 to wake


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool
    detail: str = ""


Check = Callable[[httpx.Client, str, str], Result]


def _check(name: str, ok: bool, detail: str = "") -> Result:
    return Result(name, ok, detail)


def site_answers(client: httpx.Client, site: str, api: str) -> Result:
    r = client.get(site + "/", timeout=FIRST_WAIT)
    return _check("Web app answers", r.status_code == 200 and '<div id="root"' in r.text, f"HTTP {r.status_code}")


def site_headers(client: httpx.Client, site: str, api: str) -> Result:
    h = client.get(site + "/").headers
    wanted = ("strict-transport-security", "x-content-type-options", "x-frame-options", "content-security-policy")
    missing = [name for name in wanted if name not in h]
    return _check("Web app security headers", not missing, f"missing: {', '.join(missing)}" if missing else "")


def api_alive(client: httpx.Client, site: str, api: str) -> Result:
    r = client.get(api + "/api/v1/health", timeout=FIRST_WAIT)
    return _check("API answers", r.status_code == 200 and r.json().get("status") == "ok", f"HTTP {r.status_code}")


def api_ready(client: httpx.Client, site: str, api: str) -> Result:
    r = client.get(api + "/api/v1/health/ready")
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    return _check(
        "Database and Redis reachable",
        r.status_code == 200 and body.get("database") is True and body.get("redis") is True,
        f"HTTP {r.status_code} {body}",
    )


def api_headers(client: httpx.Client, site: str, api: str) -> Result:
    h = client.get(api + "/api/v1/health").headers
    wanted = {"x-content-type-options": "nosniff", "x-frame-options": "DENY", "strict-transport-security": None}
    wrong = [k for k, v in wanted.items() if k not in h or (v is not None and h[k] != v)]
    return _check("API security headers", not wrong, f"missing or wrong: {', '.join(wrong)}" if wrong else "")


def proxy_works(client: httpx.Client, site: str, api: str) -> Result:
    r = client.get(site + "/api/v1/health")
    return _check("The web app reaches the API (/api proxy)", r.status_code == 200, f"HTTP {r.status_code}")


def public_info(client: httpx.Client, site: str, api: str) -> Result:
    r = client.get(api + "/api/v1/public/info")
    return _check("Website prices load", r.status_code == 200, f"HTTP {r.status_code}")


def docs_off(client: httpx.Client, site: str, api: str) -> Result:
    codes = [client.get(api + p).status_code for p in ("/api/docs", "/api/openapi.json")]
    return _check("API docs switched off", all(c == 404 for c in codes), f"HTTP {codes}")


def dev_routes_off(client: httpx.Client, site: str, api: str) -> Result:
    codes = [
        client.get(api + "/api/v1/dev/sms/latest").status_code,
        client.post(api + "/api/v1/dev/fake-gateway/transfers", json={}).status_code,
    ]
    return _check("Development-only routes absent", all(c in (404, 405) for c in codes), f"HTTP {codes}")


def sign_in_needed(client: httpx.Client, site: str, api: str) -> Result:
    codes = [client.get(api + p).status_code for p in ("/api/v1/auth/me", "/api/v1/students", "/api/v1/payments")]
    return _check("Staff pages need sign-in", all(c == 401 for c in codes), f"HTTP {codes}")


def forged_webhook(client: httpx.Client, site: str, api: str) -> Result:
    r = client.post(
        api + "/api/v1/webhooks/paystack",
        content=b'{"event":"charge.success","data":{"reference":"smoke-test"}}',
        headers={"x-paystack-signature": "0" * 128, "content-type": "application/json"},
    )
    return _check("Forged payment notifications refused", r.status_code in (400, 401, 403), f"HTTP {r.status_code}")


def fake_receipt(client: httpx.Client, site: str, api: str) -> Result:
    r = client.get(api + "/api/v1/public/receipts/smokeTestNotARealCode0")
    return _check("Made-up receipt codes don't verify", r.status_code == 404, f"HTTP {r.status_code}")


def https_redirect(client: httpx.Client, site: str, api: str) -> Result:
    if not site.startswith("https://"):
        return _check("HTTP redirects to HTTPS", True, "skipped: not an https site")
    r = client.get("http://" + site.removeprefix("https://") + "/", follow_redirects=False)
    ok = r.status_code in (301, 302, 307, 308) and r.headers.get("location", "").startswith("https://")
    return _check("HTTP redirects to HTTPS", ok, f"HTTP {r.status_code}")


# The wake-up checks first: everything after them can use the normal timeout
CHECKS: list[Check] = [
    api_alive,
    site_answers,
    api_ready,
    proxy_works,
    public_info,
    site_headers,
    api_headers,
    docs_off,
    dev_routes_off,
    sign_in_needed,
    forged_webhook,
    fake_receipt,
    https_redirect,
]


def run(site: str, api: str, client: httpx.Client | None = None) -> list[Result]:
    site, api = site.rstrip("/"), api.rstrip("/")
    own = client is None
    client = client or httpx.Client(timeout=30.0, follow_redirects=True, headers={"User-Agent": "wardpay-smoke"})
    results = []
    try:
        for check in CHECKS:
            try:
                results.append(check(client, site, api))
            except (httpx.HTTPError, ValueError) as e:
                results.append(Result(check.__name__.replace("_", " "), False, f"{type(e).__name__}: {e}"))
    finally:
        if own:
            client.close()
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only checks of a live deployment")
    parser.add_argument("site", help="The web app's address, e.g. https://wardpay.netlify.app")
    parser.add_argument("--api", help="The API's own address (default: the web app's, through its /api proxy)")
    args = parser.parse_args(argv)
    results = run(args.site, args.api or args.site)
    for r in results:
        print(f"{'PASS' if r.ok else 'FAIL'}  {r.name}" + (f"  ({r.detail})" if r.detail and not r.ok else ""))
    failed = sum(not r.ok for r in results)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
