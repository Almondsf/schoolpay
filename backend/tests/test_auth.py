"""Staff sign-in, sessions, MFA and passwords."""

import re

import pyotp
import pytest
from sqlalchemy import select

from app.core import email
from app.core.tenancy import scope_platform_wide, scope_to_school
from app.modules.accounts.models import AuthSession, Role
from app.modules.audit.models import AuditLog, PlatformAuditLog
from app.modules.schools.models import SchoolStatus
from tests.factories import PASSWORD, headers_for, make_school, make_user, totp_now


async def login(client, user, password=PASSWORD):
    return await client.post("/api/v1/auth/login", json={"email": user.email, "password": password})


def audit_actions(db, school_id):
    scope_to_school(db, school_id)
    return [row.action for row in db.scalars(select(AuditLog).order_by(AuditLog.created_at))]


async def test_login_without_mfa_returns_tokens_and_refresh_cookie(client, db):
    user = make_user(db, make_school(db), Role.SCHOOL_ADMIN, mfa=False)
    db.commit()
    response = await login(client, user)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "signed_in"
    assert body["user"]["role"] == "school_admin"
    assert "students.manage" in body["user"]["capabilities"]
    cookie = response.headers["set-cookie"]
    assert "sp_refresh=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "Path=/api/v1/auth" in cookie and "Secure" in cookie
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == user.email


async def test_email_is_case_insensitive(client, db):
    user = make_user(db, make_school(db))
    db.commit()
    response = await client.post("/api/v1/auth/login", json={"email": user.email.upper(), "password": PASSWORD})
    assert response.status_code == 200


async def test_wrong_password_and_unknown_email_look_the_same(client, db):
    school = make_school(db)
    user = make_user(db, school)
    db.commit()
    wrong = await login(client, user, "not-the-password")
    unknown = await client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert "auth.login_failed" in audit_actions(db, school.id)


async def test_unknown_email_failure_goes_to_platform_audit(client, db):
    await client.post("/api/v1/auth/login", json={"email": "ghost@example.com", "password": "x"})
    scope_platform_wide(db)
    labels = db.scalars(select(PlatformAuditLog.actor_label).where(PlatformAuditLog.action == "auth.login_failed"))
    assert "ghost@example.com" in list(labels)


async def test_inactive_and_invited_users_cannot_sign_in(client, db):
    school = make_school(db)
    inactive = make_user(db, school, active=False)
    invited = make_user(db, school, password=None)
    db.commit()
    assert (await login(client, inactive)).status_code == 401
    assert (await login(client, invited)).status_code == 401


async def test_login_is_rate_limited_per_account(client, db):
    user = make_user(db, make_school(db))
    db.commit()
    for _ in range(10):
        await login(client, user, "wrong-password")
    response = await login(client, user)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert int(response.headers["retry-after"]) > 0


async def test_mfa_login_two_steps_and_no_code_replay(client, db):
    user = make_user(db, make_school(db), Role.BURSAR)
    db.commit()
    first = await login(client, user)
    assert first.json()["status"] == "mfa_required"
    ticket = first.json()["mfa_ticket"]
    # The ticket is not an access token
    assert (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {ticket}"})).status_code == 401
    code = totp_now(user)
    ok = await client.post("/api/v1/auth/login/mfa", json={"mfa_ticket": ticket, "totp_code": code})
    assert ok.status_code == 200 and ok.json()["status"] == "signed_in"
    again = await client.post(
        "/api/v1/auth/login/mfa",
        json={"mfa_ticket": (await login(client, user)).json()["mfa_ticket"], "totp_code": code},
    )
    assert again.status_code == 401 and again.json()["error"]["code"] == "mfa_code_invalid"


async def test_forged_mfa_ticket_is_rejected(client, db):
    response = await client.post("/api/v1/auth/login/mfa", json={"mfa_ticket": "abc.def.ghi", "totp_code": "123456"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "mfa_ticket_invalid"


async def test_proprietor_must_enrol_in_mfa_then_gets_recovery_codes(client, db):
    school = make_school(db)
    user = make_user(db, school, Role.PROPRIETOR, mfa=False)
    db.commit()
    signed_in = (await login(client, user)).json()
    assert signed_in["user"]["mfa_enrolment_required"] is True
    headers = {"Authorization": f"Bearer {signed_in['access_token']}"}
    blocked = await client.get("/api/v1/staff", headers=headers)
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "mfa_enrolment_required"

    setup = await client.post("/api/v1/auth/mfa/setup", json={"password": PASSWORD}, headers=headers)
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_uri"].startswith("otpauth://totp/")
    confirm = await client.post(
        "/api/v1/auth/mfa/confirm", json={"totp_code": pyotp.TOTP(secret).now()}, headers=headers
    )
    assert confirm.status_code == 200
    codes = confirm.json()["recovery_codes"]
    assert len(codes) == 10
    assert (await client.get("/api/v1/staff", headers=headers)).status_code == 200

    # A recovery code signs in once
    ticket = (await login(client, user)).json()["mfa_ticket"]
    used = await client.post("/api/v1/auth/login/mfa", json={"mfa_ticket": ticket, "recovery_code": codes[0]})
    assert used.status_code == 200
    ticket = (await login(client, user)).json()["mfa_ticket"]
    reused = await client.post("/api/v1/auth/login/mfa", json={"mfa_ticket": ticket, "recovery_code": codes[0]})
    assert reused.status_code == 401
    assert {"auth.mfa_enabled", "auth.mfa_recovery_code_used"} <= set(audit_actions(db, school.id))


async def test_mfa_setup_requires_password(client, db):
    user = make_user(db, make_school(db), Role.SCHOOL_ADMIN)
    headers = headers_for(db, user)
    response = await client.post("/api/v1/auth/mfa/setup", json={"password": "wrong"}, headers=headers)
    assert response.status_code == 400 and response.json()["error"]["field"] == "password"


async def test_refresh_rotates_and_reuse_revokes_the_session(client, db):
    user = make_user(db, make_school(db), mfa=False)
    db.commit()
    signed_in = await login(client, user)
    access = signed_in.json()["access_token"]
    old_refresh = client.cookies.get("sp_refresh")
    rotated = await client.post("/api/v1/auth/refresh")
    assert rotated.status_code == 200
    new_refresh = client.cookies.get("sp_refresh")
    assert new_refresh and new_refresh != old_refresh

    # Someone replays the stolen old cookie: the whole session is killed
    client.cookies.set("sp_refresh", old_refresh, path="/api/v1/auth")
    replay = await client.post("/api/v1/auth/refresh")
    assert replay.status_code == 401
    client.cookies.set("sp_refresh", new_refresh, path="/api/v1/auth")
    assert (await client.post("/api/v1/auth/refresh")).status_code == 401
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 401
    assert "auth.refresh_reuse_detected" in audit_actions(db, user.school_id)


async def test_logout_revokes_access_token_immediately(client, db):
    user = make_user(db, make_school(db))
    headers = headers_for(db, user)
    assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=headers)).status_code == 401


async def test_password_change_signs_out_other_devices(client, db):
    user = make_user(db, make_school(db))
    this_device, other_device = headers_for(db, user), headers_for(db, user)
    weak = await client.post(
        "/api/v1/auth/password/change",
        json={"current_password": PASSWORD, "new_password": "password1"},
        headers=this_device,
    )
    assert weak.status_code == 400 and weak.json()["error"]["code"] == "weak_password"
    ok = await client.post(
        "/api/v1/auth/password/change",
        json={"current_password": PASSWORD, "new_password": "a-much-better-passphrase"},
        headers=this_device,
    )
    assert ok.status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=this_device)).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=other_device)).status_code == 401


def _token_from_last_email():
    message = email.sent_messages[-1]
    return message, re.search(r"#token=(\S+)", message.body).group(1)


async def test_password_reset_flow_and_link_is_single_use(client, db):
    user = make_user(db, make_school(db))
    session_headers = headers_for(db, user)
    response = await client.post("/api/v1/auth/password/forgot", json={"email": user.email})
    assert response.status_code == 202
    message, token = _token_from_last_email()
    assert message.to == user.email and "Reset" in message.subject
    new_password = "fresh-and-long-passphrase"
    assert (
        await client.post("/api/v1/auth/password/set", json={"token": token, "new_password": new_password})
    ).status_code == 204
    again = await client.post("/api/v1/auth/password/set", json={"token": token, "new_password": "another-good-one1"})
    assert again.status_code == 400 and again.json()["error"]["code"] == "link_invalid"
    assert (await login(client, user, new_password)).status_code == 200
    # Existing sessions end when the password is reset
    assert (await client.get("/api/v1/auth/me", headers=session_headers)).status_code == 401


async def test_forgot_password_for_unknown_email_sends_nothing(client):
    response = await client.post("/api/v1/auth/password/forgot", json={"email": "nobody@example.com"})
    assert response.status_code == 202
    assert email.sent_messages == []


async def test_reset_link_is_not_an_access_token(client, db):
    user = make_user(db, make_school(db))
    db.commit()
    await client.post("/api/v1/auth/password/forgot", json={"email": user.email})
    _, token = _token_from_last_email()
    assert (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})).status_code == 401


async def test_suspended_school_blocks_sign_in_and_existing_sessions(client, db):
    school = make_school(db)
    user = make_user(db, school)
    headers = headers_for(db, user)
    school.status = SchoolStatus.SUSPENDED
    db.commit()
    assert (await client.get("/api/v1/auth/me", headers=headers)).json()["error"]["code"] == "school_suspended"
    assert (await login(client, user)).status_code == 403


@pytest.mark.parametrize("header", ["Bearer nonsense", "Basic dXNlcjpwYXNz", ""])
async def test_bad_credentials_are_rejected(client, header):
    response = await client.get("/api/v1/auth/me", headers={"Authorization": header} if header else {})
    assert response.status_code == 401


async def test_sessions_are_recorded_with_device_details(client, db):
    user = make_user(db, make_school(db), mfa=False)
    db.commit()
    await client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": PASSWORD},
        headers={"User-Agent": "TestBrowser/1.0"},
    )
    auth_session = db.scalar(select(AuthSession).where(AuthSession.user_id == user.id))
    assert auth_session.user_agent == "TestBrowser/1.0"
    assert auth_session.ip_address == "203.0.113.10"
