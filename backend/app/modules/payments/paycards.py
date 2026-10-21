"""
Printable pay cards: one per child, for parents who don't use apps. The card carries
the child's own account number in large type (pay any amount from a bank app, a POS
agent or a bank branch) and a QR code to the parent's family payment page. Schools
print a class at a time (four cards to an A4 page) and send them home.

The QR code is the parent's family payment link, so the card says when it stops
working (`FAMILY_LINK_DAYS`): schools reprint cards each term with the bills.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import LAGOS
from app.core.errors import DomainError
from app.core.repo import get_or_404
from app.modules.receipts.pdf import qr_drawing
from app.modules.schools.models import Arm, ClassLevel, School, Term
from app.modules.students.models import Enrollment, Guardian, GuardianStudent, Student, StudentStatus

from .family_links import link_url, live_links
from .models import VirtualAccount, VirtualAccountStatus

GREEN = colors.HexColor("#0b2a21")
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
    link: str | None
    link_expires: date | None


def pay_cards(
    session: Session, *, student_id: uuid.UUID | None = None, class_level_id: uuid.UUID | None = None
) -> list[PayCard]:
    """
    Cards for one pupil or every current pupil in a class (this term), in class order.
    A fixed number of queries however many pupils; makes parents' payment links where
    needed (the caller commits).
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
    main_parent: dict[uuid.UUID, Guardian] = dict(
        session.execute(
            select(GuardianStudent.student_id, Guardian)
            .join(Guardian, Guardian.id == GuardianStudent.guardian_id)
            .where(
                GuardianStudent.student_id.in_(ids), GuardianStudent.is_primary.is_(True), Guardian.erased_at.is_(None)
            )
        ).all()
    )
    links = live_links(session, list({g.id: g for g in main_parent.values()}.values()))

    cards = []
    for student, level, arm in rows:
        account = accounts.get(student.id)
        parent = main_parent.get(student.id)
        link = links.get(parent.id) if parent else None
        cards.append(
            PayCard(
                first_name=student.first_name,
                full_name=student.full_name,
                class_name=f"{level} {arm}" if level else "",
                admission_number=student.admission_number,
                account_number=account.account_number if account else None,
                bank_name=account.bank_name if account else "",
                account_name=account.account_name if account else "",
                link=link_url(link.token) if link else None,
                link_expires=link.expires_at.astimezone(LAGOS).date() if link else None,
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
    cursor = top - 14 * mm
    if card.account_number:
        canvas.setFont("Helvetica-Bold", 7)
        canvas.drawString(x + pad, cursor, "PAY BY BANK TRANSFER TO")
        canvas.setFillColor(colors.black)
        canvas.setFont("Courier-Bold", 21)
        canvas.drawString(x + pad, cursor - 9 * mm, card.account_number)
        canvas.setFont("Helvetica-Bold", 9.5)
        canvas.drawString(x + pad, cursor - 15 * mm, _fit(card.bank_name, "Helvetica-Bold", 9.5, inner))
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(x + pad, cursor - 19.5 * mm, _fit(card.account_name, "Helvetica", 8, inner))
        note = (
            f"This number is only for {card.first_name}. Pay any amount, any time: from your bank app, "
            "a POS agent or any bank. You get a receipt by text."
        )
        cursor = _paragraph(canvas, note, x + pad, cursor - 26 * mm, inner, "Helvetica", 8, colors.black)
    else:
        canvas.setFillColor(colors.black)
        cursor = _paragraph(
            canvas,
            "Scan the code with your phone camera to see what is owed and pay by bank transfer, card or USSD.",
            x + pad,
            cursor,
            inner,
            "Helvetica",
            9,
            colors.black,
        )

    # The QR code to the family payment page
    if card.link:
        size = 30 * mm
        qx, qy = x + pad, y + 13 * mm
        drawing = qr_drawing(card.link, size)
        drawing.drawOn(canvas, qx - 2 * mm, qy)
        text_x = qx + size + 2 * mm
        width = CARD_W - pad - (text_x - x)
        lines = ["Scan to see what's owed", "and pay online"]
        canvas.setFillColor(colors.black)
        canvas.setFont("Helvetica-Bold", 8.5)
        for n, line in enumerate(lines):
            canvas.drawString(text_x, qy + size - 6 * mm - n * 4 * mm, _fit(line, "Helvetica-Bold", 8.5, width))
        if card.link_expires:
            canvas.setFillColor(MUTED)
            canvas.setFont("Helvetica", 7.5)
            canvas.drawString(text_x, qy + size - 16 * mm, f"Code works until {card.link_expires:%d %b %Y}")

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
