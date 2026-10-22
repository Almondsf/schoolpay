"""A subscription invoice as a one-page A4 PDF, with how to pay."""

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.config import get_settings
from app.modules.schools.models import School

from .billing import InvoiceRow
from .models import SubscriptionLine, SubscriptionPayment


def _ngn(kobo: int) -> str:
    whole, rest = divmod(abs(kobo), 100)
    return f"{'-' if kobo < 0 else ''}NGN {whole:,}.{rest:02d}"


def render_invoice(
    row: InvoiceRow, school: School, lines: list[SubscriptionLine], payments: list[SubscriptionPayment]
) -> bytes:
    settings = get_settings()
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9, textColor=colors.grey)
    right = ParagraphStyle("right", parent=styles["Normal"], alignment=TA_RIGHT)
    brand = ParagraphStyle("brand", parent=styles["Title"], alignment=TA_LEFT)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Invoice {row.number}",
        author=settings.app_name,
    )
    status = {"paid": "PAID", "void": "VOID", "overdue": "OVERDUE"}.get(row.status, "")
    header = Table(
        [
            [
                Paragraph(f"<b>{escape(settings.app_name)}</b>", brand),
                Paragraph(f"<b>Invoice {row.number}</b><br/>{escape(row.period)}<br/>{status}", right),
            ]
        ],
        colWidths=[100 * mm, 74 * mm],
    )
    billed_to = Paragraph(
        f"<b>Billed to</b><br/>{escape(school.name)}<br/>{escape(school.address)}<br/>{escape(school.email)}",
        styles["Normal"],
    )
    dates = Paragraph(
        f"Issued {row.issued_at:%d %B %Y}<br/>Due by <b>{row.due_date:%d %B %Y}</b><br/>Plan: {escape(row.plan_name)}",
        right,
    )
    body = [["Description", "Quantity", "Unit price", "Amount"]]
    body += [[ln.description, str(ln.quantity), _ngn(ln.unit_kobo), _ngn(ln.amount_kobo)] for ln in lines]
    body += [["", "", "Total", _ngn(row.total_kobo)]]
    for payment in payments:
        how_paid = "Paid by card" if payment.gateway else "Paid"
        body.append(["", "", f"{how_paid} ({payment.received_on:%d %b %Y})", _ngn(-payment.amount_kobo)])
    body += [["", "", "Balance due", _ngn(row.balance_kobo)]]
    table = Table(body, colWidths=[86 * mm, 20 * mm, 34 * mm, 34 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f5132")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, len(lines)), 0.25, colors.HexColor("#d0d7d3")),
                ("FONT", (2, -1), (-1, -1), "Helvetica-Bold", 10),
            ]
        )
    )
    if settings.platform_account_number:
        how = (
            f"Pay by bank transfer to <b>{escape(settings.platform_account_name)}</b>, account "
            f"<b>{escape(settings.platform_account_number)}</b> ({escape(settings.platform_bank_name)}). "
            f"Please use <b>{row.number}</b> as the reference."
        )
    else:
        how = "Contact us for payment details, quoting this invoice number."
    how += " Or pay by card from Subscription in the console."
    story = [
        header,
        Spacer(1, 6 * mm),
        Table([[billed_to, dates]], colWidths=[100 * mm, 74 * mm]),
        Spacer(1, 8 * mm),
        table,
        Spacer(1, 8 * mm),
        Paragraph(how, styles["Normal"]),
        Spacer(1, 4 * mm),
        Paragraph(
            "The set-up fee is charged once; the annual licence once per school year; the pupil charge each term "
            "is a third of the yearly fee per pupil, for the pupils enrolled that term. Parents pay nothing extra.",
            small,
        ),
    ]
    doc.build(story)
    return buffer.getvalue()
