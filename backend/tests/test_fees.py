"""Fee setup, invoice generation and the invoice ledger."""

import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.tenancy import scope_to_school
from app.modules.accounts.models import Role
from app.modules.fees.models import Discount, DiscountKind, DiscountRule, FeeItem, FeeSchedule, Invoice, InvoiceLine
from app.modules.students.models import Guardian, GuardianStudent
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

NAIRA = 100  # kobo


def add_fees(school, term, levels, *, tuition=(150_000, 180_000, 200_000)):
    """Tuition per class, an optional bus fee and a PTA levy for every class."""
    with tenant_writer() as w:
        items = {name: FeeItem(id=uuid.uuid4(), school_id=school.id, name=name) for name in ("Tuition", "Bus", "PTA")}
        w.add_all(items.values())
        w.flush()
        for level, amount in zip(levels, tuition, strict=False):
            w.add_all(
                [
                    FeeSchedule(
                        school_id=school.id,
                        term_id=term.id,
                        class_level_id=level.id,
                        fee_item_id=items["Tuition"].id,
                        amount_kobo=amount * NAIRA,
                    ),
                    FeeSchedule(
                        school_id=school.id,
                        term_id=term.id,
                        class_level_id=level.id,
                        fee_item_id=items["Bus"].id,
                        amount_kobo=30_000 * NAIRA,
                        is_optional=True,
                    ),
                    FeeSchedule(
                        school_id=school.id,
                        term_id=term.id,
                        class_level_id=level.id,
                        fee_item_id=items["PTA"].id,
                        amount_kobo=5_000 * NAIRA,
                    ),
                ]
            )
    return items


def share_guardian(school, *students):
    with tenant_writer() as w:
        guardian = Guardian(
            id=uuid.uuid4(),
            school_id=school.id,
            full_name="Mrs Parent",
            phone=f"+23480{uuid.uuid4().int % 10**9:09d}",
            phone_index=uuid.uuid4().hex[:40],
        )
        w.add(guardian)
        w.flush()
        w.add_all(
            GuardianStudent(school_id=school.id, guardian_id=guardian.id, student_id=s.id, is_primary=i == 0)
            for i, s in enumerate(students)
        )


@pytest.fixture
def world(db):
    school = make_school(db)
    bursar = make_user(db, school, Role.BURSAR)
    admin = make_user(db, school, Role.SCHOOL_ADMIN)
    _, terms = make_calendar(school)
    levels = make_levels(school)  # JSS 1 (A, B), JSS 2 (A, B), JSS 3 (A)
    items = add_fees(school, terms[0], levels)
    db.commit()
    return {
        "school": school,
        "bursar": headers_for(db, bursar),
        "admin": headers_for(db, admin),
        "terms": terms,
        "levels": levels,
        "items": items,
    }


async def generate(client, world, *, dry_run=False, term=None, **body):
    term = term or world["terms"][0]
    response = await client.post(
        f"/api/v1/terms/{term.id}/invoices/generate", headers=world["bursar"], json={"dry_run": dry_run, **body}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def account(client, world, student):
    response = await client.get(f"/api/v1/students/{student.id}/account", headers=world["bursar"])
    assert response.status_code == 200, response.text
    return response.json()


# --- Setup ---------------------------------------------------------------------------------------


async def test_fee_items_and_schedule(client, world):
    h, term, level = world["bursar"], world["terms"][1], world["levels"][0]
    lunch = (await client.post("/api/v1/fee-items", headers=h, json={"name": "Lunch", "category": "service"})).json()
    assert (await client.post("/api/v1/fee-items", headers=h, json={"name": "Lunch"})).status_code == 409
    schedule = await client.put(
        f"/api/v1/terms/{term.id}/fee-schedule",
        headers=h,
        json={
            "entries": [
                {
                    "fee_item_id": lunch["id"],
                    "class_level_id": str(level.id),
                    "amount_kobo": 2_500_000,
                    "is_optional": True,
                }
            ]
        },
    )
    assert schedule.status_code == 200
    assert schedule.json() == [
        {"fee_item_id": lunch["id"], "class_level_id": str(level.id), "amount_kobo": 2_500_000, "is_optional": True}
    ]
    updated = await client.put(
        f"/api/v1/terms/{term.id}/fee-schedule",
        headers=h,
        json={"entries": [{"fee_item_id": lunch["id"], "class_level_id": str(level.id), "amount_kobo": 3_000_000}]},
    )
    assert updated.json()[0]["amount_kobo"] == 3_000_000
    removed = await client.put(
        f"/api/v1/terms/{term.id}/fee-schedule",
        headers=h,
        json={"entries": [{"fee_item_id": lunch["id"], "class_level_id": str(level.id), "amount_kobo": None}]},
    )
    assert removed.json() == []
    bad = await client.put(
        f"/api/v1/terms/{term.id}/fee-schedule",
        headers=h,
        json={"entries": [{"fee_item_id": lunch["id"], "class_level_id": str(level.id), "amount_kobo": 0}]},
    )
    assert bad.status_code == 422


async def test_copy_schedule_keeps_existing_amounts(client, world):
    h, first, second = world["bursar"], world["terms"][0], world["terms"][1]
    tuition, level = world["items"]["Tuition"], world["levels"][0]
    await client.put(
        f"/api/v1/terms/{second.id}/fee-schedule",
        headers=h,
        json={"entries": [{"fee_item_id": str(tuition.id), "class_level_id": str(level.id), "amount_kobo": 1}]},
    )
    copied = await client.post(
        f"/api/v1/terms/{second.id}/fee-schedule/copy", headers=h, json={"from_term_id": str(first.id)}
    )
    assert copied.json() == {"copied": 8}  # 9 entries, one already set
    schedule = (await client.get(f"/api/v1/terms/{second.id}/fee-schedule", headers=h)).json()
    kept = next(e for e in schedule if e["fee_item_id"] == str(tuition.id) and e["class_level_id"] == str(level.id))
    assert kept["amount_kobo"] == 1


async def test_school_admin_sets_fees_and_bills(client, world):
    h = world["admin"]  # school admins do everything a bursar does (owner, 2026-10-20)
    assert (await client.get("/api/v1/fee-items", headers=h)).status_code == 200
    assert (await client.post("/api/v1/fee-items", headers=h, json={"name": "X Fee"})).status_code == 201
    term = world["terms"][0]
    generated = await client.post(f"/api/v1/terms/{term.id}/invoices/generate", headers=h, json={"dry_run": True})
    assert generated.status_code == 200
    assert (await client.get("/api/v1/fee-items")).status_code == 401


# --- Generation ----------------------------------------------------------------------------------


async def test_generate_term_invoices_with_options_and_discounts(client, world, db):
    school, term = world["school"], world["terms"][0]
    jss1, jss2, jss3 = world["levels"]
    eldest = make_student(school, term, jss3.arms[0], first="Eldest")
    younger = make_student(school, term, jss1.arms[0], first="Younger")
    scholar = make_student(school, term, jss2.arms[0], first="Scholar")
    bus_rider = make_student(school, term, jss1.arms[1], first="Rider")
    share_guardian(school, younger, eldest)
    db.commit()
    h = world["bursar"]
    await client.put(
        f"/api/v1/terms/{term.id}/optional-fees",
        headers=h,
        json={"fee_item_id": str(world["items"]["Bus"].id), "student_ids": [str(bus_rider.id)], "selected": True},
    )
    sibling = (
        await client.post(
            "/api/v1/discounts",
            headers=h,
            json={
                "name": "Sibling 10%",
                "kind": "percent",
                "value": 1000,
                "rule": "sibling",
                "fee_item_id": str(world["items"]["Tuition"].id),
            },
        )
    ).json()
    scholarship = (
        await client.post(
            "/api/v1/discounts", headers=h, json={"name": "Scholarship", "kind": "fixed", "value": 50_000 * NAIRA}
        )
    ).json()
    assert (
        await client.post(
            f"/api/v1/students/{scholar.id}/discounts", headers=h, json={"discount_id": scholarship["id"]}
        )
    ).status_code == 204
    assert (
        await client.post(f"/api/v1/students/{scholar.id}/discounts", headers=h, json={"discount_id": sibling["id"]})
    ).status_code == 400

    preview = await generate(client, world, dry_run=True)
    assert preview["invoices"] == 4 and preview["already_invoiced"] == 0
    expected_total = (
        (200_000 + 5_000) + (150_000 + 5_000 - 15_000) + (180_000 + 5_000 - 50_000) + (150_000 + 30_000 + 5_000)
    )
    assert preview["total_kobo"] == expected_total * NAIRA
    assert preview["discounts_kobo"] == (15_000 + 50_000) * NAIRA

    done = await generate(client, world, due_date="2026-09-30")
    assert done["invoices"] == 4
    younger_account = await account(client, world, younger)
    [invoice] = younger_account["invoices"]
    assert invoice["total_kobo"] == 140_000 * NAIRA and invoice["status"] == "unpaid"
    detail = (await client.get(f"/api/v1/invoices/{invoice['id']}", headers=h)).json()
    assert [(ln["kind"], ln["description"], ln["amount_kobo"]) for ln in detail["lines"]] == [
        ("fee", "PTA", 5_000 * NAIRA),
        ("fee", "Tuition", 150_000 * NAIRA),
        ("discount", "Sibling 10%", -15_000 * NAIRA),
    ]
    eldest_invoice = (await account(client, world, eldest))["invoices"][0]
    assert eldest_invoice["total_kobo"] == 205_000 * NAIRA  # the eldest pays in full

    # Numbers are consecutive, with no gaps
    scope_to_school(db, school.id)
    numbers = sorted(db.scalars(select(Invoice.number)))
    assert numbers == list(range(numbers[0], numbers[0] + 4))

    # Running it again creates nothing new
    again = await generate(client, world)
    assert (again["invoices"], again["already_invoiced"]) == (0, 4)


async def test_generation_skips_withdrawn_and_reports_classes_without_fees(client, world, db):
    school, term = world["school"], world["terms"][0]
    other = make_levels(school, {"Nursery 1": ["A"]}, first_rank=10)[0]
    make_student(school, term, other.arms[0], first="Tiny")
    left = make_student(school, term, world["levels"][0].arms[0], first="Gone")
    db.commit()
    await client.post(
        f"/api/v1/students/{left.id}/withdraw",
        headers=world["admin"],
        json={"left_on": "2026-09-01", "reason": "Moved away"},
    )
    report = await generate(client, world, dry_run=True)
    assert report["invoices"] == 0
    assert report["no_fees_set"] == ["Nursery 1"]


async def test_generation_is_a_fixed_number_of_queries(client, world, connection):
    school, term = world["school"], world["terms"][0]
    for i in range(4):
        make_student(school, term, world["levels"][i % 3].arms[0], first=f"S{i}")
    with QueryCounter(connection) as small:
        await generate(client, world, dry_run=True)
    for i in range(40):
        make_student(school, term, world["levels"][i % 3].arms[0], first=f"M{i}")
    with QueryCounter(connection) as big:
        await generate(client, world, dry_run=True)
    assert big.count == small.count


# --- The ledger ------------------------------------------------------------------------------------


async def test_adjustments_void_and_opening_balance(client, world, db):
    school, term = world["school"], world["terms"][0]
    student = make_student(school, term, world["levels"][0].arms[0])
    db.commit()
    await generate(client, world)
    h = world["bursar"]
    invoice = (await account(client, world, student))["invoices"][0]
    url = f"/api/v1/invoices/{invoice['id']}"

    charged = await client.post(
        f"{url}/lines",
        headers=h,
        json={"kind": "fee", "description": "Replacement ID card", "amount_kobo": 2_000 * NAIRA, "reason": "Lost card"},
    )
    assert charged.status_code == 201 and charged.json()["total_kobo"] == 157_000 * NAIRA
    waived = await client.post(
        f"{url}/lines",
        headers=h,
        json={
            "kind": "adjustment",
            "description": "PTA waived",
            "amount_kobo": -5_000 * NAIRA,
            "reason": "Approved by owner",
        },
    )
    assert waived.json()["total_kobo"] == 152_000 * NAIRA
    too_much = await client.post(
        f"{url}/lines",
        headers=h,
        json={"kind": "adjustment", "description": "Oops", "amount_kobo": -999_999 * NAIRA, "reason": "Typo test"},
    )
    assert too_much.status_code == 400

    opening = await client.post(
        "/api/v1/invoices",
        headers=h,
        json={
            "student_id": str(student.id),
            "kind": "opening_balance",
            "lines": [{"description": "Balance from 2025/2026", "amount_kobo": 40_000 * NAIRA}],
        },
    )
    assert opening.status_code == 201
    duplicate = await client.post(
        "/api/v1/invoices",
        headers=h,
        json={
            "student_id": str(student.id),
            "kind": "opening_balance",
            "lines": [{"description": "Again", "amount_kobo": 1 * NAIRA}],
        },
    )
    assert duplicate.status_code == 409

    # Arrears carry forward: everything still unpaid counts towards what's owed
    summary = await account(client, world, student)
    assert summary["outstanding_kobo"] == (152_000 + 40_000) * NAIRA
    assert [i["kind"] for i in summary["invoices"]] == ["term", "opening_balance"]

    voided = await client.post(f"{url}/void", headers=h, json={"reason": "Issued to wrong class"})
    assert voided.json()["status"] == "void"
    assert (await client.post(f"{url}/void", headers=h, json={"reason": "Again please"})).status_code == 409
    assert (await account(client, world, student))["outstanding_kobo"] == 40_000 * NAIRA
    # A voided term invoice can be replaced by generating again
    assert (await generate(client, world))["invoices"] == 1


async def test_ledger_lines_cannot_be_edited_or_deleted(client, world, db):
    make_student(world["school"], world["terms"][0], world["levels"][0].arms[0])
    db.commit()
    await generate(client, world)
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("UPDATE invoice_lines SET amount_kobo = 1"))
    db.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM invoice_lines"))
    db.rollback()


async def test_database_refuses_an_invoice_below_zero(client, world, db):
    make_student(world["school"], world["terms"][0], world["levels"][0].arms[0])
    db.commit()
    await generate(client, world)
    scope_to_school(db, world["school"].id)
    invoice = db.scalar(select(Invoice))
    # Deferred constraints fire at the real commit, which tests never reach (each test is rolled back);
    # IMMEDIATE runs the same check at the end of the statement instead
    db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    db.add(
        InvoiceLine(invoice_id=invoice.id, position=99, kind="adjustment", description="Too big", amount_kobo=-(10**12))
    )
    with pytest.raises(DBAPIError, match="less than zero"):
        db.flush()
    db.rollback()


async def test_balances_reconcile_with_the_ledger(client, world, db):
    school, term = world["school"], world["terms"][0]
    for i in range(6):
        make_student(school, term, world["levels"][i % 3].arms[0], first=f"R{i}")
    db.commit()
    await generate(client, world)
    summary = (await client.get(f"/api/v1/terms/{term.id}/billing-summary", headers=world["bursar"])).json()
    scope_to_school(db, school.id)
    ledger_total = db.scalar(select(func.sum(InvoiceLine.amount_kobo)))
    listed = (await client.get(f"/api/v1/invoices?term_id={term.id}", headers=world["bursar"])).json()
    assert summary["billed_kobo"] == ledger_total == sum(i["total_kobo"] for i in listed["items"])
    assert summary["outstanding_kobo"] == ledger_total and summary["paid_kobo"] == 0
    assert summary["invoices"] == listed["total"] == 6
    unpaid = (await client.get(f"/api/v1/invoices?status=unpaid&term_id={term.id}", headers=world["bursar"])).json()
    assert unpaid["total"] == 6


# --- School isolation ------------------------------------------------------------------------------


async def test_cannot_touch_another_schools_fees_or_invoices(client, world, db):
    other = make_school(db)
    other_bursar = make_user(db, other, Role.BURSAR)
    _, other_terms = make_calendar(other)
    other_levels = make_levels(other)
    other_items = add_fees(other, other_terms[0], other_levels)
    other_student = make_student(other, other_terms[0], other_levels[0].arms[0])
    db.commit()
    other_h = headers_for(db, other_bursar)
    await client.post(f"/api/v1/terms/{other_terms[0].id}/invoices/generate", headers=other_h, json={"dry_run": False})
    other_invoice = (await client.get("/api/v1/invoices", headers=other_h)).json()["items"][0]["id"]
    with tenant_writer() as w:
        other_discount = Discount(
            id=uuid.uuid4(),
            school_id=other.id,
            name="Theirs",
            kind=DiscountKind.FIXED,
            value=100,
            rule=DiscountRule.MANUAL,
        )
        w.add(other_discount)

    h = world["bursar"]
    my_level, my_item = world["levels"][0], world["items"]["Tuition"]
    assert (await client.get("/api/v1/invoices", headers=h)).json()["total"] == 0
    assert {i["name"] for i in (await client.get("/api/v1/fee-items", headers=h)).json()} == {"Tuition", "Bus", "PTA"}
    for method, url, body in [
        ("get", f"/api/v1/invoices/{other_invoice}", None),
        (
            "post",
            f"/api/v1/invoices/{other_invoice}/lines",
            {"kind": "fee", "description": "Sneaky", "amount_kobo": 100, "reason": "Sneaky one"},
        ),
        ("post", f"/api/v1/invoices/{other_invoice}/void", {"reason": "Sneaky void"}),
        ("get", f"/api/v1/students/{other_student.id}/account", None),
        ("get", f"/api/v1/terms/{other_terms[0].id}/fee-schedule", None),
        ("post", f"/api/v1/terms/{other_terms[0].id}/invoices/generate", {"dry_run": True}),
        ("patch", f"/api/v1/fee-items/{other_items['Tuition'].id}", {"name": "Hacked"}),
        ("patch", f"/api/v1/discounts/{other_discount.id}", {"name": "Hacked"}),
        (
            "post",
            "/api/v1/invoices",
            {
                "student_id": str(other_student.id),
                "kind": "adhoc",
                "lines": [{"description": "Sneaky", "amount_kobo": 100}],
            },
        ),
    ]:
        response = await client.request(method, url, headers=h, json=body)
        assert response.status_code == 404, (url, response.text)
    # Other schools' IDs inside request bodies are refused too
    mixed = await client.put(
        f"/api/v1/terms/{world['terms'][0].id}/fee-schedule",
        headers=h,
        json={
            "entries": [
                {"fee_item_id": str(other_items["Tuition"].id), "class_level_id": str(my_level.id), "amount_kobo": 100}
            ]
        },
    )
    assert mixed.status_code == 400 and mixed.json()["error"]["field"] == "fee_item_id"
    mixed = await client.put(
        f"/api/v1/terms/{world['terms'][0].id}/fee-schedule",
        headers=h,
        json={
            "entries": [{"fee_item_id": str(my_item.id), "class_level_id": str(other_levels[0].id), "amount_kobo": 100}]
        },
    )
    assert mixed.status_code == 400 and mixed.json()["error"]["field"] == "class_level_id"
    stolen = await client.put(
        f"/api/v1/terms/{world['terms'][0].id}/optional-fees",
        headers=h,
        json={"fee_item_id": str(world["items"]["Bus"].id), "student_ids": [str(other_student.id)], "selected": True},
    )
    assert stolen.status_code == 400


def test_number_sequences_are_gapless(db):
    from app.core.sequences import reserve

    school, other = make_school(db), make_school(db)
    db.commit()
    assert list(reserve(db, school.id, "invoice", 3)) == [1, 2, 3]
    assert list(reserve(db, school.id, "invoice", 1)) == [4]
    assert list(reserve(db, other.id, "invoice", 2)) == [1, 2]  # each school counts on its own
    db.commit()
    assert list(reserve(db, school.id, "invoice", 2)) == [5, 6]
    db.rollback()  # a transaction that fails gives its numbers back
    assert list(reserve(db, school.id, "invoice", 1)) == [5]
