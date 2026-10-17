"""Moving students into the next term, and promotion between sessions."""

import pytest
from sqlalchemy import select

from app.core.tenancy import scope_to_school
from app.modules.accounts.models import Role
from app.modules.students.models import Student, StudentStatus
from tests.factories import QueryCounter, headers_for, make_calendar, make_levels, make_school, make_student, make_user


@pytest.fixture
def world(db):
    school = make_school(db)
    admin = make_user(db, school, Role.SCHOOL_ADMIN)
    _, this_year = make_calendar(school, 2026, current_term=3)
    _, next_year = make_calendar(school, 2027, current_term=0)
    jss1, jss2, jss3 = make_levels(school, {"JSS 1": ["A", "Gold"], "JSS 2": ["A", "Blue"], "JSS 3": ["Only"]})
    third = this_year[2]
    students = {
        "jss1_a": make_student(school, third, jss1.arms[0], first="Ada"),
        "jss1_gold": make_student(school, third, jss1.arms[1], first="Gold"),  # JSS 2 has no Gold arm
        "jss2_blue": make_student(school, third, jss2.arms[1], first="Bisi"),  # JSS 3 has one arm
        "jss3": make_student(school, third, jss3.arms[0], first="Tolu"),  # final year: graduates
    }
    db.commit()
    return {
        "school": school,
        "headers": headers_for(db, admin),
        "this_year": this_year,
        "next_year": next_year,
        "levels": (jss1, jss2, jss3),
        "students": students,
    }


async def roll(client, world, to_term, from_term, *, promote, dry_run=False):
    response = await client.post(
        f"/api/v1/terms/{to_term.id}/roll-forward",
        headers=world["headers"],
        json={"from_term_id": str(from_term.id), "promote": promote, "dry_run": dry_run},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def placement(client, world, term):
    items = (await client.get(f"/api/v1/students?term_id={term.id}&limit=200", headers=world["headers"])).json()
    return {
        s["first_name"]: (s["placement"] or {}).get("class_level_name", "")
        + " "
        + (s["placement"] or {}).get("arm_name", "")
        for s in items["items"]
    }


async def test_promotion_into_a_new_session(client, world, db):
    first_term_next_year, third_term = world["next_year"][0], world["this_year"][2]
    preview = await roll(client, world, first_term_next_year, third_term, promote=True, dry_run=True)
    assert (preview["promoted"], preview["graduated"]) == (2, 1)
    assert [u["name"].split()[0] for u in preview["unplaced"]] == ["Gold"]
    assert "no arm called Gold" in preview["unplaced"][0]["reason"]
    # A dry run changes nothing: nobody is placed yet and the final-year student is still active
    assert (await placement(client, world, first_term_next_year)) == {"Ada": " ", "Gold": " ", "Bisi": " ", "Tolu": " "}

    done = await roll(client, world, first_term_next_year, third_term, promote=True)
    assert done["promoted"] == 2
    assert await placement(client, world, first_term_next_year) == {"Ada": "JSS 2 A", "Gold": " ", "Bisi": "JSS 3 Only"}
    scope_to_school(db, world["school"].id)
    graduate = db.scalar(select(Student).where(Student.id == world["students"]["jss3"].id))
    assert graduate.status == StudentStatus.GRADUATED and graduate.left_on == third_term.end_date

    again = await roll(client, world, first_term_next_year, third_term, promote=True)
    assert (again["promoted"], again["graduated"], again["already_enrolled"]) == (0, 0, 2)


async def test_within_a_session_students_keep_their_class(client, world):
    first, second = world["next_year"][0], world["next_year"][1]
    await roll(client, world, first, world["this_year"][2], promote=True)
    result = await roll(client, world, second, first, promote=False)
    assert result["moved"] == 2
    assert await placement(client, world, second) == await placement(client, world, first)


async def test_invalid_roll_forwards_are_refused(client, world):
    this_year = world["this_year"]
    backwards = await client.post(
        f"/api/v1/terms/{this_year[0].id}/roll-forward",
        headers=world["headers"],
        json={"from_term_id": str(this_year[2].id), "promote": False},
    )
    assert backwards.status_code == 400
    promote_same_session = await client.post(
        f"/api/v1/terms/{this_year[2].id}/roll-forward",
        headers=world["headers"],
        json={"from_term_id": str(this_year[1].id), "promote": True},
    )
    assert promote_same_session.status_code == 400 and promote_same_session.json()["error"]["field"] == "promote"


async def test_roll_forward_is_a_fixed_number_of_queries(client, world, connection):
    third = world["this_year"][2]
    jss1 = world["levels"][0]
    for i in range(30):  # many more students in the source term
        make_student(world["school"], third, jss1.arms[0], first=f"Extra{i}")
    with QueryCounter(connection) as counter:
        await roll(client, world, world["next_year"][0], third, promote=True, dry_run=True)
    selects = [s for s in counter.statements if s.lstrip().upper().startswith("SELECT")]
    # auth (1) + terms (2) + source enrolments, existing target enrolments, classes (+ their arms)
    assert len(selects) <= 8


async def test_cannot_roll_another_schools_terms(client, world, db):
    other = make_school(db)
    _, other_terms = make_calendar(other)
    db.commit()
    response = await client.post(
        f"/api/v1/terms/{other_terms[1].id}/roll-forward",
        headers=world["headers"],
        json={"from_term_id": str(world["this_year"][2].id), "promote": False},
    )
    assert response.status_code == 404
    response = await client.post(
        f"/api/v1/terms/{world['next_year'][0].id}/roll-forward",
        headers=world["headers"],
        json={"from_term_id": str(other_terms[0].id), "promote": True},
    )
    assert response.status_code == 404
