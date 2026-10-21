"""School calendar (sessions, terms) and class structure (levels, arms)."""

import pytest

from app.modules.accounts.models import Role
from tests.factories import QueryCounter, headers_for, make_calendar, make_levels, make_school, make_user

SESSION_BODY = {
    "name": "2027/2028",
    "start_date": "2027-09-01",
    "end_date": "2028-07-31",
    "terms": [
        {"start_date": "2027-09-06", "end_date": "2027-12-10"},
        {"start_date": "2028-01-08", "end_date": "2028-04-05"},
        {"start_date": "2028-04-23", "end_date": "2028-07-20"},
    ],
}


@pytest.fixture
def setup(db):
    a, b = make_school(db), make_school(db)
    admin_a = make_user(db, a, Role.SCHOOL_ADMIN)
    bursar_a = make_user(db, a, Role.BURSAR)
    make_user(db, b, Role.SCHOOL_ADMIN)
    _, b_terms = make_calendar(b)
    b_levels = make_levels(b)
    db.commit()
    return {
        "a": a,
        "b": b,
        "admin": headers_for(db, admin_a),
        "bursar": headers_for(db, bursar_a),
        "b_terms": b_terms,
        "b_levels": b_levels,
    }


async def test_create_session_with_terms_and_make_current(client, setup):
    response = await client.post(
        "/api/v1/academic-sessions", headers=setup["admin"], json={**SESSION_BODY, "make_first_term_current": True}
    )
    assert response.status_code == 201
    body = response.json()
    assert [t["number"] for t in body["terms"]] == [1, 2, 3]
    assert [t["is_current"] for t in body["terms"]] == [True, False, False]
    current = (await client.get("/api/v1/terms/current", headers=setup["admin"])).json()
    assert current["id"] == body["terms"][0]["id"]
    assert current["session_name"] == "2027/2028"

    second = body["terms"][1]["id"]
    assert (await client.post(f"/api/v1/terms/{second}/make-current", headers=setup["admin"])).status_code == 200
    sessions = (await client.get("/api/v1/academic-sessions", headers=setup["admin"])).json()
    assert [t["is_current"] for t in sessions[0]["terms"]] == [False, True, False]


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"name": "2027-28"}, "name"),
        ({"end_date": "2027-08-01"}, "end_date"),
        (
            {
                "terms": [
                    {"start_date": "2027-09-06", "end_date": "2027-12-10"},
                    {"start_date": "2027-12-01", "end_date": "2028-04-05"},
                    {"start_date": "2028-04-23", "end_date": "2028-07-20"},
                ]
            },
            "terms",
        ),
        (
            {
                "terms": [
                    {"start_date": "2027-08-06", "end_date": "2027-12-10"},
                    {"start_date": "2028-01-08", "end_date": "2028-04-05"},
                    {"start_date": "2028-04-23", "end_date": "2028-07-20"},
                ]
            },
            "terms",
        ),
    ],
)
async def test_invalid_sessions_are_rejected(client, setup, change, field):
    response = await client.post("/api/v1/academic-sessions", headers=setup["admin"], json={**SESSION_BODY, **change})
    assert response.status_code in (400, 422)
    assert response.json()["error"]["field"].startswith(field)


async def test_duplicate_session_name_is_a_conflict(client, setup):
    assert (
        await client.post("/api/v1/academic-sessions", headers=setup["admin"], json=SESSION_BODY)
    ).status_code == 201
    again = await client.post("/api/v1/academic-sessions", headers=setup["admin"], json=SESSION_BODY)
    assert again.status_code == 409


async def test_same_session_name_is_fine_in_another_school(client, setup):
    # School B has 2026/2027 already; school A can create its own
    body = {
        **SESSION_BODY,
        "name": "2026/2027",
        "start_date": "2026-09-01",
        "end_date": "2027-07-31",
        "terms": [
            {"start_date": "2026-09-08", "end_date": "2026-12-12"},
            {"start_date": "2027-01-05", "end_date": "2027-04-02"},
            {"start_date": "2027-04-20", "end_date": "2027-07-24"},
        ],
    }
    assert (await client.post("/api/v1/academic-sessions", headers=setup["admin"], json=body)).status_code == 201


async def test_class_levels_arms_and_order(client, setup):
    h = setup["admin"]
    jss1 = (await client.post("/api/v1/class-levels", headers=h, json={"name": "JSS 1", "arms": ["A", "Gold"]})).json()
    jss2 = (await client.post("/api/v1/class-levels", headers=h, json={"name": "JSS 2"})).json()
    assert [a["name"] for a in jss1["arms"]] == ["A", "Gold"]
    assert [a["name"] for a in jss2["arms"]] == ["A"]
    assert (jss1["rank"], jss2["rank"]) == (1, 2)
    assert (await client.post("/api/v1/class-levels", headers=h, json={"name": "JSS 1"})).status_code == 409
    assert (
        await client.post("/api/v1/class-levels", headers=h, json={"name": "X", "arms": ["A", "a"]})
    ).status_code == 400

    reordered = await client.put(
        "/api/v1/class-levels/order", headers=h, json={"class_level_ids": [jss2["id"], jss1["id"]]}
    )
    assert [lv["name"] for lv in reordered.json()] == ["JSS 2", "JSS 1"]
    incomplete = await client.put("/api/v1/class-levels/order", headers=h, json={"class_level_ids": [jss1["id"]]})
    assert incomplete.status_code == 400

    arm = (await client.post(f"/api/v1/class-levels/{jss2['id']}/arms", headers=h, json={"name": "B"})).json()
    assert (
        await client.post(f"/api/v1/class-levels/{jss2['id']}/arms", headers=h, json={"name": "B"})
    ).status_code == 409
    renamed = await client.patch(f"/api/v1/arms/{arm['id']}", headers=h, json={"name": "Blue", "is_active": False})
    assert renamed.json() == {**arm, "name": "Blue", "is_active": False}


async def test_bursar_can_view_but_not_change_structure(client, setup):
    h = setup["bursar"]
    assert (await client.get("/api/v1/class-levels", headers=h)).status_code == 200
    assert (await client.get("/api/v1/academic-sessions", headers=h)).status_code == 200
    assert (await client.post("/api/v1/class-levels", headers=h, json={"name": "JSS 1"})).status_code == 403
    assert (await client.post("/api/v1/academic-sessions", headers=h, json=SESSION_BODY)).status_code == 403


async def test_school_isolation_for_calendar_and_classes(client, setup):
    h = setup["admin"]
    b_term, b_level = setup["b_terms"][1], setup["b_levels"][0]
    b_arm = b_level.arms[0]
    assert (await client.get("/api/v1/academic-sessions", headers=h)).json() == []
    assert (await client.get("/api/v1/class-levels", headers=h)).json() == []
    assert (await client.get("/api/v1/terms/current", headers=h)).json() is None
    for method, url, body in [
        ("post", f"/api/v1/terms/{b_term.id}/make-current", None),
        ("patch", f"/api/v1/terms/{b_term.id}", {"start_date": "2027-01-05", "end_date": "2027-04-02"}),
        ("patch", f"/api/v1/academic-sessions/{b_term.session_id}", {"name": "2030/2031"}),
        ("patch", f"/api/v1/class-levels/{b_level.id}", {"name": "Hacked"}),
        ("post", f"/api/v1/class-levels/{b_level.id}/arms", {"name": "Z"}),
        ("patch", f"/api/v1/arms/{b_arm.id}", {"name": "Hacked"}),
    ]:
        response = await client.request(method, url, headers=h, json=body)
        assert response.status_code == 404, url
    reorder = await client.put("/api/v1/class-levels/order", headers=h, json={"class_level_ids": [str(b_level.id)]})
    assert reorder.status_code == 400


STANDARD = [
    *("Creche", "Pre-Nursery", "Nursery 1", "Nursery 2", "Nursery 3"),
    *(f"Primary {n}" for n in range(1, 7)),
    *("JSS 1", "JSS 2", "JSS 3", "SSS 1", "SSS 2", "SSS 3"),
]


async def test_a_new_school_sets_up_its_classes_in_one_go(client, setup):
    h = setup["admin"]
    offered = (await client.get("/api/v1/class-levels/standard", headers=h)).json()
    assert [c["name"] for c in offered] == STANDARD and not any(c["exists"] for c in offered)
    assert offered[0]["group"] == "Preparatory" and offered[-1]["group"] == "Senior secondary"

    # Untick the preparatory classes, add an extra one of their own, two arms each
    chosen = [{"name": n, "arms": ["A", "B"]} for n in STANDARD[5:]] + [{"name": "A-level"}]
    created = await client.post("/api/v1/class-levels/batch", headers=h, json={"classes": chosen})
    assert created.status_code == 201, created.text
    levels = created.json()
    assert [lv["name"] for lv in levels] == [*STANDARD[5:], "A-level"]
    assert [lv["rank"] for lv in levels] == list(range(1, 14))
    assert [a["name"] for a in levels[0]["arms"]] == ["A", "B"] and [a["name"] for a in levels[-1]["arms"]] == ["A"]
    marked = (await client.get("/api/v1/class-levels/standard", headers=h)).json()
    assert [c["name"] for c in marked if c["exists"]] == STANDARD[5:]


async def test_classes_added_later_take_their_usual_place(client, setup):
    h = setup["admin"]
    await client.post(
        "/api/v1/class-levels/batch", headers=h, json={"classes": [{"name": "Primary 1"}, {"name": "JSS 1"}]}
    )
    later = await client.post(
        "/api/v1/class-levels/batch",
        headers=h,
        json={"classes": [{"name": "Primary 2"}, {"name": "Nursery 2"}, {"name": "sss 1"}, {"name": "Year 13"}]},
    )
    assert later.status_code == 201, later.text
    assert [lv["name"] for lv in later.json()] == ["Nursery 2", "Primary 1", "Primary 2", "JSS 1", "sss 1", "Year 13"]


async def test_batch_is_checked(client, setup):
    h = setup["admin"]
    await client.post("/api/v1/class-levels/batch", headers=h, json={"classes": [{"name": "JSS 1"}]})
    clash = await client.post(
        "/api/v1/class-levels/batch", headers=h, json={"classes": [{"name": "jss 1"}, {"name": "JSS 2"}]}
    )
    assert clash.status_code == 409 and "JSS 1" not in clash.json()["error"]["message"]  # names the one they sent
    twice = await client.post("/api/v1/class-levels/batch", headers=h, json={"classes": [{"name": "X"}, {"name": "x"}]})
    assert twice.status_code == 400
    assert (await client.post("/api/v1/class-levels/batch", headers=h, json={"classes": []})).status_code == 422
    bursar = await client.post("/api/v1/class-levels/batch", headers=setup["bursar"], json={"classes": [{"name": "Y"}]})
    assert bursar.status_code == 403
    # School B's classes don't count as taken, and aren't touched
    other = await client.post("/api/v1/class-levels/batch", headers=h, json={"classes": [{"name": "JSS 3"}]})
    assert other.status_code == 201
    assert all(lv["name"] != "Hacked" for lv in other.json())


def test_a_fixed_number_of_statements(db, connection, setup):
    import uuid

    from app.core.http import RequestMeta
    from app.core.tenancy import scope_to_school
    from app.modules.accounts.deps import StaffContext
    from app.modules.schools import academics_service as service

    school = setup["a"]
    ctx = StaffContext(
        user=make_user(db, school, Role.SCHOOL_ADMIN), session_id=uuid.uuid4(), meta=RequestMeta(), school_id=school.id
    )
    db.commit()
    scope_to_school(db, school.id)
    service.add_class_levels(db, ctx, [("Primary 3", ["A"])])
    with QueryCounter(connection) as few:
        service.add_class_levels(db, ctx, [("Primary 1", ["A"])])
    with QueryCounter(connection) as many:
        service.add_class_levels(
            db, ctx, [(name, ["A", "B"]) for name in STANDARD if name not in ("Primary 1", "Primary 3")]
        )
    assert many.count == few.count
