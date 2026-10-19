"""Render a receipt (from its snapshot) as a one-page A4 PDF with a verification QR code."""

from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.config import get_settings

from .models import Receipt
from .service import verification_url
from .words import naira_in_words

LAGOS = ZoneInfo("Africa/Lagos")
METHOD_LABELS = {
    "virtual_account": "Bank transfer",
    "card": "Card",
    "cash": "Cash",
    "teller": "Bank teller",
    "pos": "POS",
    "bank_transfer": "Bank transfer",
}


def _naira(kobo: int) -> str:
    sign = "-" if kobo < 0 else ""
    naira, rest = divmod(abs(kobo), 100)
    return f"{sign}NGN {naira:,}.{rest:02d}"


def _qr(text: str, size: float) -> Drawing:
    widget = QrCodeWidget(text)
    x1, y1, x2, y2 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    drawing.add(widget)
    return drawing


def render_receipt(receipt: Receipt, *, reversed_: bool = False) -> bytes:
    snap = receipt.snapshot
    school, student, payment = snap["school"], snap["student"], snap["payment"]
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, textColor=colors.grey)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Receipt {receipt.number}",
        author=school["name"],
    )
    story = [Paragraph(f"<b>{escape(school['name'])}</b>", styles["Title"])]
    contact = " · ".join(x for x in (school["address"], school["phone"], school["email"]) if x)
    if contact:
        story.append(Paragraph(escape(contact), styles["Normal"]))
    story.append(Spacer(1, 6 * mm))
    heading = "OFFICIAL RECEIPT" + (" (REVERSED)" if reversed_ else "")
    story.append(Paragraph(f"<b>{heading}</b>", styles["Heading2"]))

    received = datetime.fromisoformat(payment["received_at"]).astimezone(LAGOS)
    details = [
        ["Receipt no.", str(receipt.number)],
        ["Date", received.strftime("%d %B %Y, %H:%M")],
        ["Received from", f"Parent/guardian of {student['full_name']}"],
        ["Student", f"{student['full_name']} ({student['admission_number']})"],
        ["Class", student["class"] or "-"],
        ["Paid by", METHOD_LABELS.get(payment["method"], payment["method"])],
        ["Reference", payment["reference"] or "-"],
    ]
    table = Table(
        [[k, Paragraph(escape(v), styles["Normal"])] for k, v in details], colWidths=[40 * mm, 120 * mm], hAlign="LEFT"
    )
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story += [table, Spacer(1, 6 * mm)]

    story.append(Paragraph(f"<b>Amount: {_naira(receipt.amount_kobo)}</b>", styles["Heading3"]))
    story.append(Paragraph(escape(naira_in_words(receipt.amount_kobo)), styles["Normal"]))
    story.append(Spacer(1, 4 * mm))

    rows = [["Applied to", "Amount"]]
    rows += [[f"Invoice #{a['invoice_number']}", _naira(a["amount_kobo"])] for a in snap["applied"]]
    if snap["credit_kobo"]:
        rows.append(["Kept as credit for future fees", _naira(snap["credit_kobo"])])
    rows.append(["Balance still owed after this payment", _naira(snap["balance_after_kobo"])])
    applied = Table(rows, colWidths=[120 * mm, 40 * mm], hAlign="LEFT")
    applied.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
                ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.black),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story += [applied, Spacer(1, 10 * mm)]

    link = verification_url(receipt.verification_code)
    verify = Table(
        [
            [
                _qr(link, 32 * mm),
                Paragraph(
                    f"<b>Check this receipt is genuine</b><br/>Scan the code or visit<br/>{escape(link)}",
                    styles["Normal"],
                ),
            ]
        ],
        colWidths=[38 * mm, 122 * mm],
        hAlign="LEFT",
    )
    verify.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [
        verify,
        Spacer(1, 6 * mm),
        Paragraph(
            f"Issued electronically by {escape(get_settings().app_name)} on behalf of the school. "
            "No signature is required.",
            small,
        ),
    ]
    doc.build(story)
    return buffer.getvalue()
