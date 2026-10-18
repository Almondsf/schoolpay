"""Receipts: numbering, PDFs, public verification and immutability."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.modules.accounts.models import Role
from app.modules.receipts.words import naira_in_words, number_in_words
from tests.factories import headers_for, make_calendar, make_levels, make_school, make_student, make_user
from tests.test_payments import connect_gateway, pay_cash

NAIRA = 100


@pytest.mark.parametrize(
    ("kobo", "words"),
    [
        (15_000_050, "One hundred and fifty thousand naira, fifty kobo only"),
        (100, "One naira only"),
        (5, "Five kobo only"),
        (100_500_000, "One million, five thousand naira only"),
        (2_100_00, "Two thousand, one hundred naira only"),
        (100_100, "One thousand and one naira only"),
    ],
)
def test_amounts_in_words(kobo, words):
    assert naira_in_words(kobo) == words


def test_number_in_words_edges():
    assert number_in_words(0) == "zero"
    assert number_in_words(1_000_000_000) == "one billion"
    assert number_in_words(999) == "nine hundred and ninety-nine"


@pytest.fixture
def world(db):
    school = make_school(db, "Grace Model College")
    bursar, admin = make_user(db, school, Role.BURSAR), make_user(db, school, Role.SCHOOL_ADMIN)
    _, terms = make_calendar(school)
    levels = make_levels(school)
    student = make_student(school, terms[0], levels[0].arms[0], first="Chiamaka", last="Okafor")
    connect_gateway(school)
    db.commit()
    return {"school": school, "student": student, "bursar": headers_for(db, bursar), "admin": headers_for(db, admin)}


async def receipt_of(client, world, payment_id):
    response = await client.get(f"/api/v1/payments/{payment_id}/receipt", headers=world["bursar"])
    assert response.status_code == 200, response.text
    return response.json()


async def test_every_payment_gets_a_consecutive_receipt(client, world):
    first = await pay_cash(client, world, 10_000)
    second = await pay_cash(client, world, 2_500)
    r1, r2 = await receipt_of(client, world, first["payment_id"]), await receipt_of(client, world, second["payment_id"])
    assert r2["number"] == r1["number"] + 1
    assert r1["amount_kobo"] == 10_000 * NAIRA and r1["reversed"] is False
    assert "/verify/" in r1["verification_url"]
    listed = (await client.get("/api/v1/payments", headers=world["bursar"])).json()["items"]
    assert {p["receipt_number"] for p in listed} == {r1["number"], r2["number"]}


async def test_receipt_pdf(client, world):
    payment = await pay_cash(client, world, 12_345)
    receipt = await receipt_of(client, world, payment["payment_id"])
    pdf = await client.get(f"/api/v1/receipts/{receipt['id']}/pdf", headers=world["bursar"])
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert f"receipt-{receipt['number']}.pdf" in pdf.headers["content-disposition"]
    assert pdf.content.startswith(b"%PDF") and len(pdf.content) > 1500


async def test_public_verification_shows_only_initials_and_follows_reversal(client, world):
    payment = await pay_cash(client, world, 50_000)
    receipt = await receipt_of(client, world, payment["payment_id"])
    code = receipt["verification_url"].rsplit("/", 1)[1]

    public = await client.get(f"/api/v1/public/receipts/{code}")  # no sign-in
    assert public.status_code == 200
    body = public.json()
    assert body == {
        "school_name": "Grace Model College",
        "receipt_number": receipt["number"],
        "amount_kobo": 50_000 * NAIRA,
        "paid_on": body["paid_on"],
        "status": "valid",
        "student_initials": "C.O.",
    }
    assert "Chiamaka" not in public.text and "Okafor" not in public.text

    await client.post(
        f"/api/v1/payments/{payment['payment_id']}/reverse", headers=world["bursar"], json={"reason": "Teller bounced"}
    )
    assert (await client.get(f"/api/v1/public/receipts/{code}")).json()["status"] == "reversed"


async def test_fake_or_malformed_codes(client):
    assert (await client.get("/api/v1/public/receipts/" + "A" * 22)).status_code == 404
    assert (await client.get("/api/v1/public/receipts/short")).status_code == 422
    assert (await client.get("/api/v1/public/receipts/" + "%27or1=1--aaaaaaaaaaa")).status_code == 422


async def test_public_verification_is_rate_limited(client):
    for _ in range(60):
        await client.get("/api/v1/public/receipts/" + "B" * 22)
    assert (await client.get("/api/v1/public/receipts/" + "B" * 22)).status_code == 429


async def test_receipts_cannot_be_altered(client, world, db):
    await pay_cash(client, world, 1_000)
    for statement in ("UPDATE receipts SET amount_kobo = 1", "DELETE FROM receipts"):
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(statement))
        db.rollback()


async def test_each_school_numbers_its_own_receipts_and_cannot_see_others(client, world, db):
    other = make_school(db)
    other_bursar, other_admin = make_user(db, other, Role.BURSAR), make_user(db, other, Role.SCHOOL_ADMIN)
    _, other_terms = make_calendar(other)
    other_student = make_student(other, other_terms[0], make_levels(other)[0].arms[0])
    connect_gateway(other)
    db.commit()
    other_world = {
        "student": other_student,
        "admin": headers_for(db, other_admin),
        "bursar": headers_for(db, other_bursar),
    }
    await pay_cash(client, world, 1_000)
    theirs = await pay_cash(client, other_world, 1_000)
    their_receipt = await receipt_of(client, other_world, theirs["payment_id"])
    assert their_receipt["number"] == 1

    assert (
        await client.get(f"/api/v1/payments/{theirs['payment_id']}/receipt", headers=world["bursar"])
    ).status_code == 404
    assert (await client.get(f"/api/v1/receipts/{their_receipt['id']}/pdf", headers=world["bursar"])).status_code == 404
