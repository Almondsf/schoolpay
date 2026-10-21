"""
Printable pay cards: one per child, for parents who don't use apps. The card carries
the child's own account number in large type (pay any amount from a bank app, a POS
agent or a bank branch) and this term's fees, each with what's left to pay, then the
total (owner, 2026-10-21: a fee list rather than a QR code). Schools print a class at
a time (four cards to an A4 page) and send them home with the bills each term.
"""

import uuid
from dataclasses import dataclass
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.repo import get_or_404
from app.modules.reports.fee_status import FeeLine, fee_breakdowns
from app.modules.schools.models import Arm, ClassLevel, School, Term
from app.modules.students.models import Enrollment, Student, StudentStatus

from .models import VirtualAccount, VirtualAccountStatus

GREEN = colors.HexColor("#0b2a21")


def ngn(kobo: int) -> str:
    """The standard PDF fonts have no naira sign; whole naira when there are no kobo."""
    naira, rest = divmod(abs(kobo), 100)
    return f"NGN {naira:,}" + (f".{rest:02d}" if rest else "")


ACCENT = colors.HexColor("#0b9467")
MUTED = colors.HexColor("#687890")


@dataclass(frozen=True)
class PayCard:
    first_name: str
    full_name: str
    class_name: str
    admission_number: str
    account_number: str | None
    bank_name: str
    account_name: str
    term_label: str | None
    fees: list[FeeLine]  # this term's, in bill order
    earlier_left_kobo: int  # earlier terms and balances brought forward

    @property
    def total_left_kobo(self) -> int:
        return sum(f.left_kobo for f in self.fees) + self.earlier_left_kobo


def pay_cards(
    session: Session, *, student_id: uuid.UUID | None = None, class_level_id: uuid.UUID | None = None
) -> list[PayCard]:
    """
    Cards for one pupil or every current pupil in a class (this term), in class order.
    A fixed number of queries however many pupils.
    """
    term_id = session.scalar(select(Term.id).where(Term.is_current.is_(True)))
    placement = (
        select(
            Enrollment.student_id,
            Enrollment.class_level_id,
            ClassLevel.name.label("level"),
            Arm.name.label("arm"),
            ClassLevel.rank,
        )
        .join(ClassLevel, ClassLevel.id == Enrollment.class_level_id)
        .join(Arm, Arm.id == Enrollment.arm_id)
        .where(Enrollment.term_id == term_id)
        .subquery()
    )
    stmt = (
        select(Student, placement.c.level, placement.c.arm)
        .outerjoin(placement, placement.c.student_id == Student.id)
        .where(Student.status == StudentStatus.ACTIVE)
        .order_by(placement.c.rank, placement.c.arm, Student.last_name, Student.first_name)
    )
    if student_id is not None:
        student = get_or_404(session, Student, student_id, label="Student")
        if student.status != StudentStatus.ACTIVE:
            raise DomainError(f"{student.full_name} isn't at the school any more.")
        stmt = stmt.where(Student.id == student_id)
    elif class_level_id is not None:
        get_or_404(session, ClassLevel, class_level_id, label="Class")
        if term_id is None:
            raise DomainError("Set the current term first (Calendar), so pupils have a class.")
        stmt = stmt.where(placement.c.class_level_id == class_level_id)
    rows = session.execute(stmt).all()
    ids = [student.id for student, _, _ in rows]
    if not ids:
        return []

    accounts = {
        a.student_id: a
        for a in session.scalars(
            select(VirtualAccount).where(
                VirtualAccount.student_id.in_(ids), VirtualAccount.status == VirtualAccountStatus.ACTIVE
            )
        )
    }
    owed = fee_breakdowns(session, ids, term_id)

    cards = []
    for student, level, arm in rows:
        account = accounts.get(student.id)
        breakdown = owed.get(student.id)
        cards.append(
            PayCard(
                first_name=student.first_name,
                full_name=student.full_name,
                class_name=f"{level} {arm}" if level else "",
                admission_number=student.admission_number,
                account_number=account.account_number if account else None,
                bank_name=account.bank_name if account else "",
                account_name=account.account_name if account else "",
                term_label=breakdown.term_label if breakdown else None,
                fees=breakdown.fees if breakdown else [],
                earlier_left_kobo=breakdown.earlier_left_kobo if breakdown else 0,
            )
        )
    return cards


# --- PDF -------------------------------------------------------------------------------------------

CARD_W, CARD_H = 92 * mm, 136 * mm
MARGIN_X, MARGIN_Y, GAP = 9 * mm, 9 * mm, 8 * mm


def render_pay_cards(school: School, cards: list[PayCard]) -> bytes:
    """Four cards to an A4 page, with dashed outlines to cut along."""
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=A4)
    canvas.setTitle(f"{school.name} pay cards")
    canvas.setAuthor(school.name)
    _, page_h = A4
    for i, card in enumerate(cards):
        if i and i % 4 == 0:
            canvas.showPage()
        col, row = i % 2, (i % 4) // 2
        x = MARGIN_X + col * (CARD_W + GAP)
        y = page_h - MARGIN_Y - (row + 1) * CARD_H - row * GAP
        _card(canvas, x, y, school, card)
    canvas.save()
    return buffer.getvalue()


def _card(canvas: Canvas, x: float, y: float, school: School, card: PayCard) -> None:
    pad = 6 * mm
    inner = CARD_W - 2 * pad
    canvas.saveState()
    canvas.setDash(3, 3)
    canvas.setStrokeColor(colors.HexColor("#b8c2cf"))
    canvas.roundRect(x, y, CARD_W, CARD_H, 4 * mm)
    canvas.setDash()

    # Header: the school
    band = 20 * mm
    canvas.setFillColor(GREEN)
    canvas.roundRect(x, y + CARD_H - band, CARD_W, band, 4 * mm, stroke=0, fill=1)
    canvas.rect(x, y + CARD_H - band, CARD_W, 4 * mm, stroke=0, fill=1)  # square off the bottom corners
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(x + pad, y + CARD_H - 9 * mm, _fit(school.name, "Helvetica-Bold", 11, inner))
    canvas.setFont("Helvetica", 8)
    canvas.drawString(x + pad, y + CARD_H - 14.5 * mm, "School fees pay card")

    # The child
    top = y + CARD_H - band - 9 * mm
    canvas.setFillColor(colors.black)
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(x + pad, top, _fit(card.full_name, "Helvetica-Bold", 13, inner))
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8.5)
    who = " · ".join(part for part in (card.class_name, card.admission_number) if part)
    canvas.drawString(x + pad, top - 5 * mm, _fit(who, "Helvetica", 8.5, inner))

    # How to pay
    cursor = top - 13 * mm
    if card.account_number:
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawString(x + pad, cursor, "PAY BY BANK TRANSFER TO")
        canvas.setFillColor(colors.black)
        canvas.setFont("Courier-Bold", 20)
        canvas.drawString(x + pad, cursor - 8.5 * mm, card.account_number)
        canvas.setFont("Helvetica-Bold", 9)
        bank = " · ".join(part for part in (card.bank_name, card.account_name) if part)
        canvas.drawString(x + pad, cursor - 13.5 * mm, _fit(bank, "Helvetica-Bold", 9, inner))
        note = f"Only for {card.first_name}. Pay any amount, any time, from your bank app, a POS agent or any bank."
        cursor = _paragraph(canvas, note, x + pad, cursor - 18.5 * mm, inner, "Helvetica", 7.5, MUTED)
    else:
        cursor = _paragraph(
            canvas,
            "Pay at the school's bursary. Ask the school for the account to pay into.",
            x + pad,
            cursor,
            inner,
            "Helvetica",
            8.5,
            colors.black,
        )

    # What is to be paid this term
    cursor -= 3 * mm
    canvas.setStrokeColor(colors.HexColor("#e6eaf0"))
    canvas.line(x + pad, cursor + 2.5 * mm, x + CARD_W - pad, cursor + 2.5 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica-Bold", 7)
    heading = f"FEES FOR {card.term_label.upper()}" if card.term_label else "FEES"
    canvas.drawString(x + pad, cursor - 1 * mm, _fit(heading, "Helvetica-Bold", 7, inner))
    canvas.drawRightString(x + CARD_W - pad, cursor - 1 * mm, "TO PAY")
    cursor -= 6 * mm
    rows = [(f.name, f.left_kobo, f.billed_kobo) for f in card.fees]
    if card.earlier_left_kobo:
        rows.append(("Earlier terms", card.earlier_left_kobo, card.earlier_left_kobo))
    room = max(int((cursor - (y + 22 * mm)) // (4.6 * mm)), 1)  # lines that fit above the total
    shown = rows if len(rows) <= room else rows[: room - 1]
    for name, left, billed in shown:
        canvas.setFillColor(colors.black if left > 0 else MUTED)
        canvas.setFont("Helvetica", 8.5)
        amount = ngn(left) if left > 0 else ("Paid" if billed > 0 else "-")
        canvas.drawString(x + pad, cursor, _fit(name, "Helvetica", 8.5, inner - 28 * mm))
        canvas.drawRightString(x + CARD_W - pad, cursor, amount)
        cursor -= 4.6 * mm
    if len(shown) < len(rows):
        rest = rows[len(shown) :]
        canvas.setFillColor(colors.black)
        canvas.drawString(x + pad, cursor, f"{len(rest)} more")
        canvas.drawRightString(x + CARD_W - pad, cursor, ngn(sum(left for _, left, _ in rest)))
    if not rows:
        canvas.setFillColor(MUTED)
        canvas.drawString(x + pad, cursor, "No bill for this term yet.")

    # The total
    total_y = y + 15 * mm
    canvas.setStrokeColor(colors.black)
    canvas.line(x + pad, total_y + 5 * mm, x + CARD_W - pad, total_y + 5 * mm)
    canvas.setFillColor(colors.black)
    canvas.setFont("Helvetica-Bold", 10.5)
    canvas.drawString(x + pad, total_y, "Total to pay")
    canvas.drawRightString(
        x + CARD_W - pad, total_y, ngn(card.total_left_kobo) if card.total_left_kobo > 0 else "Nothing"
    )

    # Footer: who to ask
    if school.phone:
        canvas.setFillColor(ACCENT)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawString(
            x + pad, y + 6 * mm, _fit(f"Questions? Call the school: {school.phone}", "Helvetica-Bold", 8, inner)
        )
    canvas.restoreState()


def _fit(text: str, font: str, size: float, width: float) -> str:
    """Shorten a line that wouldn't fit, ending it with an ellipsis."""
    if stringWidth(text, font, size) <= width:
        return text
    while text and stringWidth(text + "…", font, size) > width:
        text = text[:-1]
    return text + "…"


def _paragraph(
    canvas: Canvas, text: str, x: float, y: float, width: float, font: str, size: float, colour: object
) -> float:
    canvas.setFillColor(colour)
    canvas.setFont(font, size)
    for line in simpleSplit(text, font, size, width):
        canvas.drawString(x, y, line)
        y -= size * 1.35
    return y
