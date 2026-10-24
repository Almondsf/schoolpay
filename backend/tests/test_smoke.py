"""The live-deployment smoke check: passes a healthy site, and catches a badly configured one."""

import httpx

from app import smoke

SECURE = {
    "strict-transport-security": "max-age=31536000",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'self'",
}


def deployment(*, docs_on: bool = False, dev_on: bool = False, redis: bool = True) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if request.url.scheme == "http":
            return httpx.Response(301, headers={"location": str(request.url.copy_with(scheme="https"))})
        if path == "/":
            return httpx.Response(200, text='<div id="root"></div>', headers=SECURE)
        if path == "/api/v1/health":
            return httpx.Response(200, json={"status": "ok"}, headers=SECURE)
        if path == "/api/v1/health/ready":
            return httpx.Response(200 if redis else 503, json={"database": True, "redis": redis})
        if path == "/api/v1/public/info":
            return httpx.Response(200, json={})
        if path in ("/api/docs", "/api/openapi.json"):
            return httpx.Response(200 if docs_on else 404)
        if path.startswith("/api/v1/dev/"):
            return httpx.Response(200 if dev_on else 404)
        if path in ("/api/v1/auth/me", "/api/v1/students", "/api/v1/payments"):
            return httpx.Response(401)
        if path == "/api/v1/webhooks/paystack" and method == "POST":
            return httpx.Response(400)
        if path.startswith("/api/v1/public/receipts/"):
            return httpx.Response(404)
        return httpx.Response(404)

    return httpx.MockTransport(handle)


def results(transport: httpx.MockTransport) -> dict[str, bool]:
    with httpx.Client(transport=transport, follow_redirects=True) as client:
        return {r.name: r.ok for r in smoke.run("https://wardpay.example", "https://wardpay.example", client)}


def test_a_healthy_deployment_passes_every_check():
    found = results(deployment())
    assert all(found.values()), [name for name, ok in found.items() if not ok]
    assert len(found) == len(smoke.CHECKS)


def test_docs_dev_routes_and_redis_trouble_are_caught():
    found = results(deployment(docs_on=True, dev_on=True, redis=False))
    failed = {name for name, ok in found.items() if not ok}
    assert failed == {"API docs switched off", "Development-only routes absent", "Database and Redis reachable"}
