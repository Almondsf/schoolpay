"""Printable pay cards: what each card carries, a class at a time, isolation, audit, fixed queries."""

from sqlalchemy import func, select

from app.core.tenancy import scope_to_school
from app.modules.accounts.models import Role
from app.modules.audit.models import AuditAction, AuditLog
from app.modules.payments import paycards
from app.modules.payments.models import FamilyPayLink
from tests.factories import QueryCounter, headers_for, make_levels, make_school, make_student, make_user
from tests.test_reports import NAIRA
from tests.test_reports import world as world  # Ada paid in full, Bayo paid 50k of 185k, Chidi owes all + 40k earlier


async def test_one_pupils_card(client, world, db):
    bayo = world["students"]["bayo"]
    provisioned = await client.post("/api/v1/virtual-accounts/provision", headers=world["bursar"], json={})
    assert provisioned.status_code == 202

    response = await client.get(f"/api/v1/students/{bayo.id}/pay-card", headers=world["bursar"])
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"%PDF")
    assert response.headers["cache-control"] == "no-store"
    assert f'filename="pay-card-{bayo.admission_number}.pdf"' in response.headers["content-disposition"]

    scope_to_school(db, world["school"].id)
    [card] = paycards.pay_cards(db, student_id=bayo.id)
    assert card.full_name == "Bayo Ojo" and card.class_name.startswith("JSS 2")
    assert card.account_number and card.account_number.startswith("9") and card.bank_name == "Fake Bank"
    # What's to be paid: each fee this term with what's left on it, then the total
    assert card.term_label == "2026/2027 First term"
    assert {f.name: f.left_kobo for f in card.fees} == {"PTA": 5_000 * NAIRA, "Tuition": 130_000 * NAIRA}
    assert card.earlier_left_kobo == 0 and card.total_left_kobo == 135_000 * NAIRA
    # Printing makes no payment links (the card carries no code)
    assert db.scalar(select(func.count()).select_from(FamilyPayLink)) == 0
    audited = db.scalars(select(AuditLog).where(AuditLog.action == AuditAction.REPORT_EXPORTED)).all()
    assert [e.changes["report"] for e in audited] == ["pay_cards"]


async def test_earlier_debts_and_no_account_number(client, world, db):
    scope_to_school(db, world["school"].id)
    [card] = paycards.pay_cards(db, student_id=world["students"]["chidi"].id)
    assert card.account_number is None
    assert card.earlier_left_kobo == 40_000 * NAIRA and card.total_left_kobo == 245_000 * NAIRA
    assert paycards.render_pay_cards(world["school"], [card]).startswith(b"%PDF")


async def test_a_class_at_a_time(client, world):
    jss1 = world["levels"][0]
    response = await client.get("/api/v1/pay-cards", headers=world["admin"], params={"class_level_id": str(jss1.id)})
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    no_class = await client.get("/api/v1/pay-cards", headers=world["admin"])
    assert no_class.status_code == 422


async def test_another_schools_pupils_and_classes_are_not_found(client, world, db):
    other = make_school(db)
    make_levels(other)
    staff = headers_for(db, make_user(db, other, Role.BURSAR))
    db.commit()
    bayo, jss1 = world["students"]["bayo"], world["levels"][0]
    assert (await client.get(f"/api/v1/students/{bayo.id}/pay-card", headers=staff)).status_code == 404
    response = await client.get("/api/v1/pay-cards", headers=staff, params={"class_level_id": str(jss1.id)})
    assert response.status_code == 404


def test_a_fixed_number_of_queries(world, db, connection):
    scope_to_school(db, world["school"].id)
    levels, term = world["levels"], world["terms"][0]
    for n in range(6):
        make_student(world["school"], term, levels[0].arms[0], first=f"Extra{n}", last="Pupil")
    db.commit()
    with QueryCounter(connection) as one:
        paycards.pay_cards(db, student_id=world["students"]["ada"].id)
    with QueryCounter(connection) as many:
        cards = paycards.pay_cards(db, class_level_id=levels[0].id)
    assert len(cards) == 7
    assert many.count == one.count  # the same few statements, however many pupils
