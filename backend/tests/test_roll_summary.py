"""The school at a glance (no money): counts, classes, records needing attention, isolation, fixed queries."""

import uuid
from datetime import date

import pytest
from sqlalchemy import update

from app.modules.accounts.models import Role
from app.modules.payments.gateways.base import GatewayCode
from app.modules.payments.models import VirtualAccount
from app.modules.students.models import Enrollment, Gender, Guardian, Student
from tests.factories import (
    QueryCounter,
    headers_for,
    make_calendar,
    make_levels,
    make_school,
    make_student,
    make_user,
    tenant_writer,
)

URL = "/api/v1/school/roll"


def enrol(student, term, arm):
    with tenant_writer() as w:
        w.add(
            Enrollment(
                school_id=student.school_id,
                student_id=student.id,
                term_id=term.id,
                arm_id=arm.id,
                class_level_id=arm.class_level_id,
            )
        )


@pytest.fixture
def world(db):
    a, b = make_school(db, "Alpha"), make_school(db, "Beta")
    admin = make_user(db, a, Role.SCHOOL_ADMIN)
    b_admin = make_user(db, b, Role.SCHOOL_ADMIN)
    _, terms = make_calendar(a, current_term=2)
    levels = make_levels(a)  # JSS 1 (A, B), JSS 2 (A, B), JSS 3 (A)
    _, b_terms = make_calendar(b)
    make_student(b, b_terms[0], make_levels(b)[0].arms[0], first="Bola")
    db.commit()
    return {
        "school": a,
        "admin": headers_for(db, admin),
        "b_admin": headers_for(db, b_admin),
        "terms": terms,
        "levels": levels,
    }


async def add_with_parent(client, world, arm, admission, first="Chiamaka"):
    response = await client.post(
        "/api/v1/students",
        headers=world["admin"],
        json={
            "admission_number": admission,
            "first_name": first,
            "last_name": "Okafor",
            "gender": "female",
            "arm_id": str(arm.id),
            "consent_confirmed": True,
            "guardians": [{"full_name": "Ngozi Okafor", "phone": "0803 123 4567", "relationship": "mother"}],
        },
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def test_counts_classes_and_records_needing_attention(client, world, db):
    first, second = world["terms"][0], world["terms"][1]
    jss1a, jss1b = world["levels"][0].arms
    jss2a = world["levels"][1].arms[0]

    # Two siblings with a consenting parent, placed this term (added now, so they joined this term)
    await add_with_parent(client, world, jss1a, "A1")
    await add_with_parent(client, world, jss2a, "A2", first="Emeka")
    # Here since first term, no parent, a boy, with an account number
    old = make_student(world["school"], first, jss1a, first="Tunde")
    enrol(old, second, jss1b)
    # On roll but not placed this term
    make_student(world["school"], first, jss1a, first="Unplaced")
    # Left since the term started, and left last term
    gone = make_student(world["school"], second, jss1a, first="Gone")
    gone_earlier = make_student(world["school"], first, jss1a, first="Earlier")
    with tenant_writer() as w:
        w.execute(update(Student).where(Student.id == old.id).values(gender=Gender.MALE))
        w.execute(update(Student).where(Student.id == gone.id).values(status="withdrawn", left_on=date(2027, 2, 1)))
        w.execute(
            update(Student).where(Student.id == gone_earlier.id).values(status="withdrawn", left_on=date(2026, 12, 1))
        )
        w.add(
            VirtualAccount(
                school_id=world["school"].id,
                student_id=old.id,
                gateway=GatewayCode.FAKE,
                account_number="9900000001",
                account_name="Tunde",
                bank_name="Test Bank",
                provider_reference="ref-1",
            )
        )
        # The siblings' parent hasn't actually consented
        w.execute(
            update(Guardian)
            .where(Guardian.school_id == world["school"].id)
            .values(consent_at=None, consent_method=None)
        )

    response = await client.get(URL, headers=world["admin"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["term_id"] == str(second.id)
    assert body["term_label"] == "2026/2027 Second term"
    assert body["on_roll"] == 4
    assert (body["placed"], body["unplaced"]) == (3, 1)
    assert body["joined_this_term"] == 2
    assert body["left_this_term"] == 1
    assert body["no_parent"] == 2  # Tunde and Unplaced
    assert body["no_consent"] == 4  # nobody's parent has consented now
    assert body["no_account_number"] == 3
    assert body["parents"] == 1  # siblings share one parent (matched by phone)

    classes = {c["class_name"]: c for c in body["by_class"]}
    assert list(classes) == ["JSS 1", "JSS 2", "JSS 3"]  # promotion order, empty classes included
    assert (classes["JSS 1"]["pupils"], classes["JSS 1"]["boys"], classes["JSS 1"]["girls"]) == (2, 1, 1)
    assert [(a["arm_name"], a["pupils"]) for a in classes["JSS 1"]["arms"]] == [("A", 1), ("B", 1)]
    assert classes["JSS 3"]["pupils"] == 0


async def test_school_without_a_current_term(client, db):
    school = make_school(db)
    make_calendar(school, current_term=0)
    admin = make_user(db, school, Role.SCHOOL_ADMIN)
    db.commit()
    body = (await client.get(URL, headers=headers_for(db, admin))).json()
    assert body["term_id"] is None and body["by_class"] == []
    assert body["on_roll"] == 0


async def test_only_the_users_own_school_is_counted(client, world):
    body = (await client.get(URL, headers=world["b_admin"])).json()
    assert body["on_roll"] == 1
    assert [c["class_name"] for c in body["by_class"]] == ["JSS 1", "JSS 2", "JSS 3"]
    assert body["by_class"][0]["pupils"] == 1  # Bola, in Beta's current term
    mine = (await client.get(URL, headers=world["admin"])).json()
    assert mine["on_roll"] == 0


async def test_every_staff_role_can_see_it_but_not_anonymous(client, world, db):
    for role in (Role.PROPRIETOR, Role.BURSAR):
        user = make_user(db, world["school"], role)
        db.commit()
        assert (await client.get(URL, headers=headers_for(db, user))).status_code == 200
    assert (await client.get(URL)).status_code == 401


async def test_fixed_number_of_statements(client, world, db, connection):
    with QueryCounter(connection) as small:
        await client.get(URL, headers=world["admin"])
    for i in range(15):
        make_student(world["school"], world["terms"][1], world["levels"][i % 3].arms[0], first=f"More{i}")
    with QueryCounter(connection) as big:
        await client.get(URL, headers=world["admin"])
    assert big.count == small.count


async def test_student_list_filters_by_missing_record(client, world):
    arm = world["levels"][0].arms[0]
    await add_with_parent(client, world, arm, "P1")
    make_student(world["school"], world["terms"][1], arm, first="Orphan")

    async def names(missing):
        response = await client.get("/api/v1/students", headers=world["admin"], params={"missing": missing})
        assert response.status_code == 200, response.text
        return [s["first_name"] for s in response.json()["items"]]

    assert await names("parent") == ["Orphan"]
    assert await names("consent") == ["Orphan"]  # Chiamaka's parent consented when she was added
    assert sorted(await names("account_number")) == ["Chiamaka", "Orphan"]
    bad = await client.get("/api/v1/students", headers=world["admin"], params={"missing": "bvn"})
    assert bad.status_code == 422
