"""Extra charges after the term starts: class charges (WAEC), exemptions, and individual extras (uniforms)."""

import pytest
from sqlalchemy import select

from app.core.tenancy import scope_to_school
from app.modules.accounts.models import Role
from app.modules.fees.models import FeeSchedule, StudentFeeExemption
from tests.factories import QueryCounter, headers_for, make_calendar, make_levels, make_school, make_student, make_user
from tests.test_fees import account, add_fees, generate
from tests.test_payments import connect_gateway, pay_cash

NAIRA = 100
WAEC = 25_000 * NAIRA


@pytest.fixture
async def world(db, client):
    school = make_school(db, "Alpha College")
    bursar, admin = make_user(db, school, Role.BURSAR), make_user(db, school, Role.SCHOOL_ADMIN)
    _, terms = make_calendar(school)
    levels = make_levels(school)  # JSS 1 (A, B), JSS 2 (A, B), JSS 3 (A)
    items = add_fees(school, terms[0], levels)
    jss3 = [make_student(school, terms[0], levels[2].arms[0], first=f"Senior{i}") for i in range(3)]
    jss1 = make_student(school, terms[0], levels[0].arms[0], first="Junior")
    connect_gateway(school)
    db.commit()
    w = {
        "school": school,
        "terms": terms,
        "levels": levels,
        "items": items,
        "jss3": jss3,
        "jss1": jss1,
        "student": jss1,
        "bursar": headers_for(db, bursar),
        "admin": headers_for(db, admin),
    }
    await generate(client, w)
    return w


async def class_charge(client, world, *, dry_run=False, excluded=(), **body):
    payload = {
        "new_item_name": "WAEC",
        "amount_kobo": WAEC,
        "class_level_ids": [str(world["levels"][2].id)],
        "excluded_student_ids": [str(s.id) for s in excluded],
        "dry_run": dry_run,
    } | body
    return await client.post(
        f"/api/v1/terms/{world['terms'][0].id}/class-charges", headers=world["bursar"], json=payload
    )


def waec_lines(acct):
    return [line for inv in acct["invoices"] for line in inv.get("lines", []) if "WAEC" in line["description"]]


async def owed(client, world, student):
    return (await account(client, world, student))["outstanding_kobo"]


# --- Class charges --------------------------------------------------------------------------------


async def test_preview_lists_the_class_and_saves_nothing(client, world, db):
    preview = await class_charge(client, world, dry_run=True, excluded=[world["jss3"][0]])
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert len(body["students"]) == 3 and all(s["billed"] for s in body["students"])
    assert [s["excluded"] for s in body["students"]].count(True) == 1
    assert body["charged_now"] == 2 and body["total_kobo"] == 2 * WAEC
    scope_to_school(db, world["school"].id)
    assert db.scalar(select(FeeSchedule.id).where(FeeSchedule.amount_kobo == WAEC)) is None


async def test_charge_goes_on_issued_bills_except_the_excluded(client, world, db):
    excluded = world["jss3"][0]
    before = [await owed(client, world, s) for s in world["jss3"]]
    response = await class_charge(client, world, excluded=[excluded])
    assert response.status_code == 200, response.text
    after = [await owed(client, world, s) for s in world["jss3"]]
    assert after[0] == before[0]  # excluded
    assert after[1] - before[1] == WAEC and after[2] - before[2] == WAEC
    assert await owed(client, world, world["jss1"]) == 155_000 * NAIRA  # other classes untouched

    # The fee is now on the term's list for JSS 3, and the exclusion is remembered
    scope_to_school(db, world["school"].id)
    assert db.scalar(select(FeeSchedule.amount_kobo).where(FeeSchedule.class_level_id == world["levels"][2].id,
                                                          FeeSchedule.amount_kobo == WAEC)) == WAEC  # fmt: skip
    assert db.get(StudentFeeExemption, (excluded.id, world["terms"][0].id, db.scalar(
        select(FeeSchedule.fee_item_id).where(FeeSchedule.amount_kobo == WAEC)))) is not None  # fmt: skip

    # A pupil joining JSS 3 later gets it when their bill is generated
    newcomer = make_student(world["school"], world["terms"][0], world["levels"][2].arms[0], first="Late")
    db.commit()
    await generate(client, world)
    assert await owed(client, world, newcomer) - 0 == (200_000 + 5_000 + 25_000) * NAIRA

    # The same fee twice for the same class is refused
    item_id = db.scalar(select(FeeSchedule.fee_item_id).where(FeeSchedule.amount_kobo == WAEC))
    again = await class_charge(client, world, new_item_name=None, fee_item_id=str(item_id))
    assert again.status_code == 409


async def test_credit_pays_the_new_charge(client, world):
    senior = world["jss3"][1]
    await pay_cash(client, world | {"student": senior}, 205_000 + 10_000)  # bill + ₦10k ahead
    assert await owed(client, world, senior) == 0
    await class_charge(client, world)
    assert await owed(client, world, senior) == WAEC - 10_000 * NAIRA


async def test_class_charges_are_a_fixed_number_of_statements(client, world, db, connection):
    with QueryCounter(connection) as small:
        await class_charge(client, world, dry_run=True)
    for i in range(20):
        make_student(world["school"], world["terms"][0], world["levels"][2].arms[0], first=f"More{i}")
    db.commit()
    await generate(client, world)
    with QueryCounter(connection) as big:
        await class_charge(client, world, dry_run=True)
    assert big.count == small.count


# --- Exemptions ------------------------------------------------------------------------------------


async def test_exempting_after_billing_takes_it_off_and_undoing_puts_it_back(client, world, db):
    await class_charge(client, world)
    senior = world["jss3"][2]
    scope_to_school(db, world["school"].id)
    item_id = str(db.scalar(select(FeeSchedule.fee_item_id).where(FeeSchedule.amount_kobo == WAEC)))
    term_id = str(world["terms"][0].id)
    url = f"/api/v1/students/{senior.id}/exemptions"

    off = await client.post(
        url,
        headers=world["bursar"],
        json={"term_id": term_id, "fee_item_id": item_id, "reason": "Sitting NECO instead"},
    )
    assert off.status_code == 201 and off.json() == {"changed_kobo": WAEC}
    assert await owed(client, world, senior) == 205_000 * NAIRA
    listed = (await client.get(url, headers=world["bursar"])).json()
    assert [(e["item_name"], e["reason"]) for e in listed] == [("WAEC", "Sitting NECO instead")]
    assert (
        await client.post(url, headers=world["bursar"], json={"term_id": term_id, "fee_item_id": item_id})
    ).status_code == 409

    back = await client.delete(f"{url}/{item_id}", headers=world["bursar"], params={"term_id": term_id})
    assert back.json() == {"changed_kobo": WAEC}
    assert await owed(client, world, senior) == (205_000 + 25_000) * NAIRA


async def test_cant_take_a_fee_off_a_bill_already_paid_beyond_it(client, world, db):
    await class_charge(client, world)
    senior = world["jss3"][1]
    await pay_cash(client, world | {"student": senior}, 230_000)  # the whole bill, WAEC included
    scope_to_school(db, world["school"].id)
    item_id = str(db.scalar(select(FeeSchedule.fee_item_id).where(FeeSchedule.amount_kobo == WAEC)))
    refused = await client.post(
        f"/api/v1/students/{senior.id}/exemptions",
        headers=world["bursar"],
        json={"term_id": str(world["terms"][0].id), "fee_item_id": item_id},
    )
    assert refused.status_code == 409 and "paid" in refused.json()["error"]["message"]


async def test_an_exemption_before_billing_leaves_the_fee_off(client, world, db):
    newcomer = make_student(world["school"], world["terms"][0], world["levels"][0].arms[0], first="New")
    db.commit()
    pta = str(world["items"]["PTA"].id)
    response = await client.post(
        f"/api/v1/students/{newcomer.id}/exemptions",
        headers=world["bursar"],
        json={"term_id": str(world["terms"][0].id), "fee_item_id": pta, "reason": "Staff child"},
    )
    assert response.json() == {"changed_kobo": 0}  # not billed yet: nothing to take off
    await generate(client, world)
    assert await owed(client, world, newcomer) == 150_000 * NAIRA  # tuition only, no PTA


# --- Individual extras -----------------------------------------------------------------------------


async def test_price_list_items_go_on_the_term_bill(client, world):
    created = await client.post(
        "/api/v1/fee-items",
        headers=world["bursar"],
        json={"name": "School uniform", "category": "other", "default_amount_kobo": 8_000 * NAIRA},
    )
    assert created.status_code == 201 and created.json()["default_amount_kobo"] == 8_000 * NAIRA
    uniform = created.json()["id"]
    pupil = world["jss1"]
    charged = await client.post(
        f"/api/v1/students/{pupil.id}/charges", headers=world["bursar"], json={"fee_item_id": uniform, "quantity": 2}
    )
    assert charged.status_code == 201, charged.text
    invoice = (await client.get(f"/api/v1/invoices/{charged.json()['invoice_id']}", headers=world["bursar"])).json()
    assert invoice["kind"] == "term"
    assert invoice["lines"][-1]["description"] == "School uniform x 2"
    assert invoice["lines"][-1]["amount_kobo"] == 16_000 * NAIRA

    # The price can be cleared or changed later
    cleared = await client.patch(
        f"/api/v1/fee-items/{uniform}", headers=world["bursar"], json={"default_amount_kobo": None}
    )
    assert cleared.json()["default_amount_kobo"] is None
    no_price = await client.post(
        f"/api/v1/students/{pupil.id}/charges", headers=world["bursar"], json={"fee_item_id": uniform}
    )
    assert no_price.status_code == 400 and no_price.json()["error"]["field"] == "unit_kobo"


async def test_a_pupil_not_billed_this_term_gets_a_one_off_bill(client, world, db):
    newcomer = make_student(world["school"], world["terms"][0], world["levels"][0].arms[0], first="Unbilled")
    db.commit()
    charged = await client.post(
        f"/api/v1/students/{newcomer.id}/charges",
        headers=world["bursar"],
        json={"description": "Textbooks", "unit_kobo": 4_500 * NAIRA, "quantity": 3},
    )
    invoice = (await client.get(f"/api/v1/invoices/{charged.json()['invoice_id']}", headers=world["bursar"])).json()
    assert invoice["kind"] == "adhoc" and invoice["total_kobo"] == 13_500 * NAIRA


# --- Access ----------------------------------------------------------------------------------------


async def test_only_billing_staff_and_only_their_school(client, world, db):
    pupil = world["jss1"]
    admin = await client.post(f"/api/v1/students/{pupil.id}/charges", headers=world["admin"],
                              json={"description": "Books", "unit_kobo": 100_000})  # fmt: skip
    assert admin.status_code in (200, 201), admin.text  # school admins bill too (owner, 2026-10-20)
    other = make_school(db, "Beta Academy")
    theirs = headers_for(db, make_user(db, other, Role.BURSAR))
    db.commit()
    assert (await client.post(f"/api/v1/students/{pupil.id}/charges", headers=theirs,
                              json={"description": "Books", "unit_kobo": 100})).status_code == 404  # fmt: skip
    cross = await client.post(
        f"/api/v1/terms/{world['terms'][0].id}/class-charges",
        headers=theirs,
        json={"new_item_name": "WAEC", "amount_kobo": WAEC, "class_level_ids": [str(world["levels"][2].id)]},
    )
    assert cross.status_code == 404  # another school's term
    assert (await client.get(f"/api/v1/students/{pupil.id}/exemptions", headers=theirs)).status_code == 404


# --- Catching bills up with the fee list -------------------------------------------------------


async def waec_item(client, world):
    item = await client.post("/api/v1/fee-items", headers=world["bursar"], json={"name": "WAEC"})
    assert item.status_code == 201, item.text
    return item.json()["id"]


async def schedule_waec(client, world, *, optional=False, item_id=None):
    """What the user did: add WAEC to JSS 3 in the fee grid after the bills went out. Returns what saving did."""
    item_id = item_id or await waec_item(client, world)
    entry = {"fee_item_id": item_id, "class_level_id": str(world["levels"][2].id), "amount_kobo": WAEC,
             "is_optional": optional}  # fmt: skip
    saved = await client.put(
        f"/api/v1/terms/{world['terms'][0].id}/fee-schedule", headers=world["bursar"], json={"entries": [entry]}
    )
    assert saved.status_code == 200, saved.text
    return saved.json()["bills"]


async def sync(client, world, dry_run):
    response = await client.post(
        f"/api/v1/terms/{world['terms'][0].id}/bills/sync", headers=world["bursar"], json={"dry_run": dry_run}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_fee_added_to_a_class_after_billing_goes_on_every_bill_in_it(client, world, db):
    """Owner, 2026-10-21: a fee added for a class shows on every pupil in that class, at once."""
    added = await schedule_waec(client, world)
    assert added["fees"] == [{"item_name": "WAEC", "class_names": ["JSS 3"], "bills": 3, "total_kobo": 3 * WAEC}]
    assert added["bills_changed"] == 3 and added["dry_run"] is False
    assert all([await owed(client, world, s) == 205_000 * NAIRA + WAEC for s in world["jss3"]])
    assert await owed(client, world, world["jss1"]) == 155_000 * NAIRA  # other classes untouched
    assert (await sync(client, world, dry_run=True))["bills_changed"] == 0  # nothing left to add


async def test_exempt_pupils_and_untaken_optional_fees_are_left_out(client, world, db):
    item_id = await waec_item(client, world)
    exempt = await client.post(
        f"/api/v1/students/{world['jss3'][0].id}/exemptions",
        headers=world["bursar"],
        json={"fee_item_id": item_id, "term_id": str(world["terms"][0].id), "reason": "Not sitting"},
    )
    assert exempt.status_code in (200, 201), exempt.text
    assert (await schedule_waec(client, world, item_id=item_id))["bills_changed"] == 2
    assert await owed(client, world, world["jss3"][0]) == 205_000 * NAIRA


async def test_optional_fee_only_for_pupils_who_chose_it(client, world):
    assert (await schedule_waec(client, world, optional=True))["bills_changed"] == 0


async def test_sync_uses_money_paid_ahead_and_stays_in_the_school(client, world, db):
    senior = world["jss3"][1]
    await pay_cash(client, world, 205_000 + 25_000, student=senior)  # bill + WAEC, ahead of time
    await schedule_waec(client, world)
    assert await owed(client, world, senior) == 0  # the credit covered the new WAEC line

    other = make_user(db, make_school(db, "Beta Academy"), Role.BURSAR)
    db.commit()
    stranger = await client.post(
        f"/api/v1/terms/{world['terms'][0].id}/bills/sync", headers=headers_for(db, other), json={"dry_run": True}
    )
    assert stranger.status_code == 404


async def test_saving_the_grid_is_a_fixed_number_of_statements(client, world, db, connection):
    """Saving a class fee that goes onto 3 bills or 18 costs the same statements."""
    waec = await waec_item(client, world)
    with QueryCounter(connection) as small:
        assert (await schedule_waec(client, world, item_id=waec))["bills_changed"] == 3
    for i in range(15):
        make_student(world["school"], world["terms"][0], world["levels"][2].arms[0], first=f"More{i}")
    db.commit()
    await generate(client, world)
    neco = await client.post("/api/v1/fee-items", headers=world["bursar"], json={"name": "NECO"})
    with QueryCounter(connection) as big:
        assert (await schedule_waec(client, world, item_id=neco.json()["id"]))["bills_changed"] == 18
    assert big.count == small.count
