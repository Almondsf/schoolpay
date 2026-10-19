"""Schools signing themselves up, confirming by email, and the getting-started checklist."""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core import email
from app.core.tenancy import scope_platform_wide
from app.modules.accounts.models import Role, User
from app.modules.audit.models import PlatformAuditLog
from app.modules.platform import signup
from app.modules.schools.models import School, SchoolOrigin, SchoolStatus
from tests.factories import headers_for, make_calendar, make_levels, make_school, make_student, make_user

FORM = {
    "school_name": "Little Angels Academy",
    "proprietor_name": "Mrs Funke Bello",
    "email": "Funke@LittleAngels.example.com",
    "phone": "0803 123 4567",
    "address": "4 Bode Thomas Street, Surulere",
    "accept_terms": True,
}


async def sign_up(client, **changes):
    return await client.post("/api/v1/public/signup", json=FORM | changes)


def the_school(db, name="Little Angels Academy"):
    scope_platform_wide(db)
    db.expire_all()
    return db.scalar(select(School).where(School.name == name))


async def test_signup_creates_a_pending_school_until_the_email_is_confirmed(client, db):
    response = await sign_up(client)
    assert response.status_code == 202
    school = the_school(db)
    assert school.status == SchoolStatus.PENDING and school.origin == SchoolOrigin.SIGNUP
    assert school.slug == "little-angels-academy" and school.email == "funke@littleangels.example.com"
    message = email.sent_messages[-1]
    assert message.to == "funke@littleangels.example.com" and "Confirm your school" in message.subject

    # Can't sign in before confirming (no password yet)
    token = re.search(r"#token=(\S+)", message.body).group(1)
    confirmed = await client.post(
        "/api/v1/auth/password/set", json={"token": token, "new_password": "angels-are-great-1"}
    )
    assert confirmed.status_code == 204
    assert the_school(db).status == SchoolStatus.ACTIVE
    login = await client.post(
        "/api/v1/auth/login", json={"email": "funke@littleangels.example.com", "password": "angels-are-great-1"}
    )
    assert login.status_code == 200, login.text  # proprietors are asked to turn on two-factor sign-in next
    trail = set(db.scalars(select(PlatformAuditLog.action)))
    assert {"school.signed_up", "school.activated"} <= trail


async def test_same_answer_for_an_email_that_already_has_an_account(client, db):
    existing = make_user(db, make_school(db), Role.BURSAR, email="taken@example.com")
    db.commit()
    response = await sign_up(client, email="taken@example.com", school_name="Copycat School")
    assert response.status_code == 202
    assert response.json() == (await sign_up(client, email="new@example.com", school_name="Fresh School")).json()
    assert the_school(db, "Copycat School") is None
    warned = [m for m in email.sent_messages if m.to == "taken@example.com"]
    assert warned and "already have" in warned[-1].subject
    assert existing.id


async def test_bots_and_missing_consent(client, db):
    bot = await sign_up(client, website="http://spam.example", school_name="Spam School")
    assert bot.status_code == 202 and the_school(db, "Spam School") is None  # quietly ignored
    no_terms = await sign_up(client, accept_terms=False, school_name="Hasty School")
    assert no_terms.status_code == 403 and the_school(db, "Hasty School") is None
    bad_phone = await sign_up(client, phone="call me", school_name="Typo School")
    assert bad_phone.status_code == 422


async def test_signup_is_rate_limited_per_address(client):
    for i in range(5):
        assert (await sign_up(client, email=f"s{i}@example.com", school_name=f"School {i}")).status_code == 202
    assert (await sign_up(client, email="s9@example.com", school_name="School 9")).status_code == 429


async def test_similar_names_get_their_own_short_name(client, db):
    await sign_up(client)
    await sign_up(client, email="other@example.com")
    scope_platform_wide(db)
    slugs = sorted(db.scalars(select(School.slug).where(School.name == "Little Angels Academy")))
    assert slugs == ["little-angels-academy", "little-angels-academy-2"]


async def test_unconfirmed_signups_are_removed(client, db):
    await sign_up(client)
    await sign_up(client, email="confirmed@example.com", school_name="Confirmed School")
    scope_platform_wide(db)
    db.execute(
        update(School)
        .values(created_at=datetime.now(UTC) - timedelta(days=30))
        .where(School.origin == SchoolOrigin.SIGNUP)
    )
    db.execute(update(User).where(User.email == "confirmed@example.com").values(password_hash="set-by-the-proprietor"))
    db.commit()
    assert signup.prune_pending() == 1
    assert the_school(db) is None
    assert the_school(db, "Confirmed School") is not None  # its proprietor had set a password


# --- Getting started ------------------------------------------------------------------------------------


async def test_checklist_follows_the_schools_own_data(client, db):
    fresh = make_school(db, "Brand New School")
    admin = make_user(db, fresh, Role.SCHOOL_ADMIN)
    busy = make_school(db, "Busy School")
    _, terms = make_calendar(busy)
    make_student(busy, terms[0], make_levels(busy)[0].arms[0])
    make_user(db, busy, Role.BURSAR)
    db.commit()
    steps = (await client.get("/api/v1/school/onboarding", headers=headers_for(db, admin))).json()
    assert [s["key"] for s in steps][:3] == ["profile", "calendar", "classes"]
    assert not any(s["done"] for s in steps)  # another school's calendar, students and bursar don't count

    _, my_terms = make_calendar(fresh)
    make_levels(fresh)
    db.commit()
    done = {
        s["key"]: s["done"]
        for s in (await client.get("/api/v1/school/onboarding", headers=headers_for(db, admin))).json()
    }
    assert done["calendar"] and done["classes"] and not done["students"] and not done["staff"]
    assert my_terms
