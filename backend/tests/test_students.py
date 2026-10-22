"""Students, enrolments and guardians: behaviour, permissions and school isolation."""

import pytest
from sqlalchemy import text

from app.modules.accounts.models import Role
from tests.factories import headers_for, make_calendar, make_levels, make_school, make_student, make_user


@pytest.fixture
def world(db):
    a, b = make_school(db, "Alpha"), make_school(db, "Beta")
    admin = make_user(db, a, Role.SCHOOL_ADMIN)
    b_admin = make_user(db, b, Role.SCHOOL_ADMIN)
    bursar = make_user(db, a, Role.BURSAR)
    _, a_terms = make_calendar(a)
    a_levels = make_levels(a)
    _, b_terms = make_calendar(b)
    b_levels = make_levels(b)
    b_student = make_student(b, b_terms[0], b_levels[0].arms[0], admission="B-001", first="Bola", last="Ade")
    db.commit()
    return {
        "admin": headers_for(db, admin),
        "b_admin": headers_for(db, b_admin),
        "bursar": headers_for(db, bursar),
        "a_terms": a_terms,
        "a_levels": a_levels,
        "b_terms": b_terms,
        "b_levels": b_levels,
        "b_student": b_student,
    }


def student_body(arm, **overrides):
    return {
        "admission_number": "ADM/001",
        "first_name": "Chiamaka",
        "last_name": "Okafor",
        "gender": "female",
        "arm_id": str(arm),
        "consent_confirmed": True,
        "guardians": [{"full_name": "Ngozi Okafor", "phone": "0803 123 4567", "relationship": "mother"}],
        **overrides,
    }


async def create(client, world, **overrides):
    arm = world["a_levels"][0].arms[0]
    return await client.post("/api/v1/students", headers=world["admin"], json=student_body(arm.id, **overrides))


async def test_create_student_with_guardian(client, world):
    response = await create(client, world)
    assert response.status_code == 201
    body = response.json()
    assert body["full_name"] == "Chiamaka Okafor"
    [guardian] = body["guardians"]
    assert guardian["phone"] == "+2348031234567"  # normalised
    assert guardian["is_primary"] is True and guardian["relationship"] == "mother"
    assert guardian["consent_at"] is not None
    [enrollment] = body["enrollments"]
    assert enrollment["placement"]["class_level_name"] == "JSS 1" and enrollment["is_current"] is True


async def test_guardian_contact_details_are_encrypted_at_rest(client, world, db):
    await create(client, world, guardians=[{"full_name": "Ngozi", "phone": "08031234567", "email": "n@example.com"}])
    raw = db.execute(text("SELECT phone, email, phone_index FROM guardians")).one()
    assert "8031234567" not in raw.phone and "example.com" not in raw.email
    assert "8031234567" not in raw.phone_index


async def test_siblings_share_one_guardian(client, world):
    first = (await create(client, world)).json()
    second = (
        await create(
            client,
            world,
            admission_number="ADM/002",
            first_name="Emeka",
            guardians=[{"full_name": "Mrs Okafor", "phone": "+2348031234567"}],
        )
    ).json()
    assert first["guardians"][0]["id"] == second["guardians"][0]["id"]
    listing = (await client.get("/api/v1/guardians?search=08031234567", headers=world["admin"])).json()
    [guardian] = listing["items"]
    assert {s["first_name"] for s in guardian["students"]} == {"Chiamaka", "Emeka"}
    assert guardian["phone_masked"] == "+234 *** *** 4567"


async def test_guardians_need_consent(client, world):
    response = await create(client, world, consent_confirmed=False)
    assert response.status_code == 400 and response.json()["error"]["code"] == "consent_required"


async def test_duplicate_admission_number_and_bad_phone(client, world):
    assert (await create(client, world)).status_code == 201
    duplicate = await create(client, world, guardians=[])
    assert duplicate.status_code == 409 and duplicate.json()["error"]["field"] == "admission_number"
    bad_phone = await create(
        client, world, admission_number="ADM/9", guardians=[{"full_name": "X Y", "phone": "12345"}]
    )
    assert bad_phone.status_code in (400, 422)


async def test_list_search_filter_and_unplaced(client, world):
    levels, terms = world["a_levels"], world["a_terms"]
    await create(client, world)
    jss2_arm = levels[1].arms[1]
    await client.post(
        "/api/v1/students",
        headers=world["admin"],
        json=student_body(jss2_arm.id, admission_number="ADM/002", first_name="Tunde", last_name="Bello", guardians=[]),
    )
    h = world["admin"]
    everyone = (await client.get("/api/v1/students", headers=h)).json()
    assert everyone["total"] == 2
    found = (await client.get("/api/v1/students?search=tunde", headers=h)).json()["items"]
    assert [s["first_name"] for s in found] == ["Tunde"]
    in_jss2 = (await client.get(f"/api/v1/students?class_level_id={levels[1].id}", headers=h)).json()["items"]
    assert [s["placement"]["arm_name"] for s in in_jss2] == ["B"]
    # Nobody is enrolled in the second term yet
    unplaced = (await client.get(f"/api/v1/students?term_id={terms[1].id}&unplaced=true", headers=h)).json()
    assert unplaced["total"] == 2 and all(s["placement"] is None for s in unplaced["items"])


async def test_change_class_withdraw_and_reinstate(client, world):
    student = (await create(client, world)).json()
    h, other_arm = world["admin"], world["a_levels"][0].arms[1]
    moved = await client.put(f"/api/v1/students/{student['id']}/class", headers=h, json={"arm_id": str(other_arm.id)})
    assert moved.status_code == 200
    detail = (await client.get(f"/api/v1/students/{student['id']}", headers=h)).json()
    assert detail["enrollments"][0]["placement"]["arm_name"] == "B"
    withdrawn = await client.post(
        f"/api/v1/students/{student['id']}/withdraw",
        headers=h,
        json={"left_on": "2026-11-01", "reason": "Family relocated"},
    )
    assert withdrawn.json()["status"] == "withdrawn"
    assert (await client.get("/api/v1/students", headers=h)).json()["total"] == 0
    assert (await client.get("/api/v1/students?status=withdrawn", headers=h)).json()["total"] == 1
    reinstated = await client.post(f"/api/v1/students/{student['id']}/reinstate", headers=h)
    assert reinstated.json()["status"] == "active" and reinstated.json()["left_on"] is None


async def test_guardian_link_unlink_primary_and_phone_change_is_audited(client, world):
    h = world["admin"]
    student = (await create(client, world)).json()
    sid = student["id"]
    added = await client.post(
        f"/api/v1/students/{sid}/guardians",
        headers=h,
        json={
            "full_name": "Chukwu Okafor",
            "phone": "08099990000",
            "relationship": "father",
            "consent_confirmed": True,
        },
    )
    assert added.status_code == 201
    father = next(g for g in added.json()["guardians"] if g["relationship"] == "father")
    assert father["is_primary"] is False
    relink = await client.post(
        f"/api/v1/students/{sid}/guardians",
        headers=h,
        json={"full_name": "Chukwu", "phone": "+2348099990000", "consent_confirmed": True},
    )
    assert relink.status_code == 409

    assert (await client.post(f"/api/v1/students/{sid}/guardians/{father['id']}/primary", headers=h)).status_code == 204
    detail = (await client.get(f"/api/v1/students/{sid}", headers=h)).json()
    assert [g["is_primary"] for g in detail["guardians"]] == [True, False]
    assert detail["guardians"][0]["id"] == father["id"]

    changed = await client.patch(f"/api/v1/guardians/{father['id']}", headers=h, json={"phone": "07011112222"})
    assert changed.status_code == 200

    # Removing the primary guardian promotes the other one
    assert (await client.delete(f"/api/v1/students/{sid}/guardians/{father['id']}", headers=h)).status_code == 204
    detail = (await client.get(f"/api/v1/students/{sid}", headers=h)).json()
    assert [g["is_primary"] for g in detail["guardians"]] == [True]


async def test_phone_change_is_audited_without_the_numbers(client, world, db):
    h = world["admin"]
    student = (await create(client, world)).json()
    guardian_id = student["guardians"][0]["id"]
    await client.patch(f"/api/v1/guardians/{guardian_id}", headers=h, json={"phone": "07011112222"})
    row = db.execute(text("SELECT changes::text FROM audit_log WHERE action = 'guardians.contact_changed'")).scalar()
    assert row is not None and "phone" in row
    assert "7011112222" not in row and "8031234567" not in row


async def test_bursar_can_view_students_but_not_change_them(client, world):
    h = world["bursar"]
    assert (await client.get("/api/v1/students", headers=h)).status_code == 200
    arm = world["a_levels"][0].arms[0]
    assert (await client.post("/api/v1/students", headers=h, json=student_body(arm.id))).status_code == 403


async def test_cannot_reach_another_schools_students_or_use_their_ids(client, world):
    h, b_student = world["admin"], world["b_student"]
    b_arm, b_term = world["b_levels"][0].arms[0], world["b_terms"][0]
    assert (await client.get("/api/v1/students?search=bola", headers=h)).json()["total"] == 0
    assert (await client.get(f"/api/v1/students?term_id={b_term.id}", headers=h)).status_code == 404
    for method, url, body in [
        ("get", f"/api/v1/students/{b_student.id}", None),
        ("patch", f"/api/v1/students/{b_student.id}", {"first_name": "Hacked"}),
        ("put", f"/api/v1/students/{b_student.id}/class", {"arm_id": str(world["a_levels"][0].arms[0].id)}),
        ("post", f"/api/v1/students/{b_student.id}/withdraw", {"left_on": "2026-10-01", "reason": "Hack attempt"}),
        (
            "post",
            f"/api/v1/students/{b_student.id}/guardians",
            {"full_name": "Evil Person", "phone": "08000000001", "consent_confirmed": True},
        ),
    ]:
        response = await client.request(method, url, headers=h, json=body)
        assert response.status_code == 404, url
    # Another school's arm or term in a request body is "not found" too
    with_b_arm = await create(client, world, arm_id=str(b_arm.id), admission_number="X1")
    assert with_b_arm.status_code == 404 and with_b_arm.json()["error"]["field"] == "arm_id"
    with_b_term = await create(client, world, term_id=str(b_term.id), admission_number="X2")
    assert with_b_term.status_code == 404 and with_b_term.json()["error"]["field"] == "term_id"


async def test_same_guardian_phone_is_separate_per_school(client, world):
    """A parent with children at two schools is a separate guardian in each; neither school sees the other's."""
    a_child = (await create(client, world)).json()
    b_arm = world["b_levels"][0].arms[0]
    b_child = (
        await client.post(
            "/api/v1/students",
            headers=world["b_admin"],
            json=student_body(b_arm.id, admission_number="B-777", first_name="Kelechi"),
        )
    ).json()
    assert a_child["guardians"][0]["id"] != b_child["guardians"][0]["id"]
    a_view = (await client.get("/api/v1/guardians?search=08031234567", headers=world["admin"])).json()["items"]
    assert [s["first_name"] for s in a_view[0]["students"]] == ["Chiamaka"]
    b_guardian = b_child["guardians"][0]["id"]
    assert (
        await client.patch(f"/api/v1/guardians/{b_guardian}", headers=world["admin"], json={"full_name": "Hacked"})
    ).status_code == 404


# --- Suggested admission numbers ---------------------------------------------------------------


def test_next_admission_number_rules():
    from datetime import UTC, date, datetime

    from app.modules.students.service import next_admission_number as nxt

    today = date(2026, 10, 22)
    t1, t2 = datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)
    assert nxt([("DMC/2026/0192", t1)], "x", today) == "DMC/2026/0193"
    # A spreadsheet import: one time for everyone; the highest number of the latest pattern wins
    imported = [("DMC/2024/0001", t1), ("DMC/2026/0240", t1), ("DMC/2026/0007", t1), ("DMC/2025/0300", t1)]
    assert nxt(imported, "x", today) == "DMC/2026/0241"
    # Pupils added later in an older pattern still count as the latest
    assert nxt([("DMC/2026/0240", t1), ("OLD-12", t2)], "x", today) == "OLD-13"
    assert nxt([("DMC/2026/0192A", t1)], "x", today) == "DMC/2026/0193A"  # letters after the digits stay
    assert nxt([("0099", t1)], "x", today) == "0100" and nxt([("999", t1)], "x", today) == "1000"
    assert nxt([("A-5", t1), ("a-6", t1), ("A-7", t2)], "x", today) == "A-8"  # taken in any case
    assert nxt([], "Sunrise Academy of Lagos", today) == "SAL/2026/0001"  # no pupils yet
    assert nxt([("SMITH", t1)], "The School of Excellence", today) == "E/2026/0001"  # nothing to count on
    assert nxt([], "", today) == "ADM/2026/0001"


def test_suggestion_follows_the_schools_own_latest_pupil(db):
    from datetime import date

    from app.core.tenancy import scope_to_school
    from app.modules.students.service import suggest_admission_number

    school, other = make_school(db, "Sunrise Academy"), make_school(db)
    _, terms = make_calendar(school)
    make_student(other, make_calendar(other)[1][0], make_levels(other)[0].arms[0], admission="SA/2026/0007")
    db.commit()
    scope_to_school(db, school.id)
    assert suggest_admission_number(db, school.name, date(2026, 10, 22)) == "SA/2026/0001"  # other schools don't count
    make_student(school, terms[0], make_levels(school)[0].arms[0], admission="SA/2026/0041")
    assert suggest_admission_number(db, school.name, date(2026, 10, 22)) == "SA/2026/0042"


async def test_admission_suggestion_endpoint(client, db):
    school = make_school(db, "Bright Future School")
    admin, bursar = make_user(db, school, Role.SCHOOL_ADMIN), make_user(db, school, Role.BURSAR)
    db.commit()
    url = "/api/v1/students/admission-number/suggestion"
    response = await client.get(url, headers=headers_for(db, admin))
    assert response.status_code == 200 and response.json()["suggestion"].startswith("BF/")
    assert (await client.get(url, headers=headers_for(db, bursar))).status_code == 403  # bursars don't add pupils
