"""Paying for particular fees: the money goes where the payer chose, the views and receipt say so."""

from sqlalchemy import select

from app.core.tenancy import scope_to_school
from app.modules.payments.gateways import FakeGateway
from app.modules.receipts.models import Receipt
from tests.test_reports import NAIRA
from tests.test_reports import world as world  # the fixture: Chidi owes 205,000 this term and 40,000 from last


async def fees_of(client, world, student):
    body = (await client.get(f"/api/v1/reports/students/{student.id}/fee-breakdown", headers=world["bursar"])).json()
    return {f["name"]: f for f in body["fees"]}, body


async def record_cash(client, world, student, naira, fees):
    recorded = await client.post(
        "/api/v1/manual-payments",
        headers=world["admin"],
        json={
            "student_id": str(student.id),
            "amount_kobo": naira * NAIRA,
            "method": "cash",
            "received_on": "2026-10-01",
            "fees": fees,
        },
    )
    assert recorded.status_code == 201, recorded.text
    assert recorded.json()["for_fees"] == fees
    approved = await client.post(f"/api/v1/manual-payments/{recorded.json()['id']}/approve", headers=world["bursar"])
    assert approved.status_code == 200, approved.text
    return approved.json()


async def test_cash_for_tuition_pays_tuition_even_with_older_debts(client, world, db):
    chidi = world["students"]["chidi"]
    before, _ = await fees_of(client, world, chidi)
    assert before["Tuition"]["left_kobo"] == 200_000 * NAIRA

    payment = await record_cash(client, world, chidi, 200_000, ["Tuition"])
    fees, body = await fees_of(client, world, chidi)
    assert fees["Tuition"]["status"] == "Paid"
    assert fees["PTA"]["status"] == "Unpaid"
    assert body["earlier_left_kobo"] == 40_000 * NAIRA  # last session's debt is still owed, as chosen

    scope_to_school(db, world["school"].id)
    receipt = db.scalar(select(Receipt).where(Receipt.payment_id == payment["payment_id"]))
    assert receipt.snapshot["for_fees"] == ["Tuition"]
    pdf = await client.get(f"/api/v1/receipts/{receipt.id}/pdf", headers=world["bursar"])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


async def test_part_of_a_fee_and_reversal(client, world):
    chidi = world["students"]["chidi"]
    payment = await record_cash(client, world, chidi, 50_000, ["Tuition"])
    fees, _ = await fees_of(client, world, chidi)
    assert fees["Tuition"]["left_kobo"] == 150_000 * NAIRA and fees["PTA"]["status"] == "Unpaid"

    reversed_ = await client.post(
        f"/api/v1/payments/{payment['payment_id']}/reverse", headers=world["bursar"], json={"reason": "Fake notes"}
    )
    assert reversed_.status_code in (200, 201), reversed_.text
    fees, _ = await fees_of(client, world, chidi)
    assert fees["Tuition"]["left_kobo"] == 200_000 * NAIRA  # the earmark no longer counts


async def test_choices_are_checked(client, world):
    chidi = world["students"]["chidi"]

    async def refused(naira, fees):
        response = await client.post(
            "/api/v1/manual-payments",
            headers=world["admin"],
            json={
                "student_id": str(chidi.id),
                "amount_kobo": naira * NAIRA,
                "method": "cash",
                "received_on": "2026-10-01",
                "fees": fees,
            },
        )
        assert response.status_code == 400, response.text
        return response.json()["error"]["message"]

    assert "nothing is owed" in await refused(1_000, ["Swimming"])
    assert "pay up to" in await refused(6_000, ["PTA"])  # PTA is 5,000


async def test_online_payment_for_chosen_fees(client, world):
    """The family page / portal: one child's share earmarked for tuition only."""
    from tests.test_parents import bearer, sign_in

    bayo = world["students"]["bayo"]
    parent = bearer(await sign_in(client, phone="08030000002"))
    family = (await client.get("/api/v1/parent/family", headers=parent)).json()
    assert [c["first_name"] for c in family["children"]] == ["Bayo"]
    started = await client.post(
        "/api/v1/parent/family/checkout",
        headers=parent,
        json={"items": [{"student_id": str(bayo.id), "amount_kobo": 130_000 * NAIRA, "fees": ["Tuition"]}]},
    )
    assert started.status_code == 201, started.text
    reference = started.json()["reference"]
    FakeGateway.shared().complete_checkout(reference)
    assert (await client.post("/api/v1/payments/verify", json={"reference": reference})).json()["status"] == "succeeded"
    fees, body = await fees_of(client, world, bayo)
    assert fees["Tuition"]["status"] == "Paid"  # the 50,000 paid earlier went to tuition first; this paid the rest
    assert fees["PTA"]["status"] == "Unpaid" and body["total_left_kobo"] == 5_000 * NAIRA
