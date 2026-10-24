"""The journal as an Excel workbook (journal, account totals, chart) or a flat CSV for accounting software."""

import csv
from io import StringIO

from openpyxl import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.modules.reports.exports import DEBTOR_HEADERS, _day, _debtor_row, _save, _sheet, naira, safe_text

from .journal import Journal, lines
from .session_pack import SessionPack

CSV = "text/csv"
JOURNAL_HEADERS = [
    ("Date", 13),
    ("Entry", 9),
    ("Reference", 16),
    ("Account code", 13),
    ("Account", 32),
    ("Description", 44),
    ("Debit (NGN)", 15),
    ("Credit (NGN)", 15),
]


def journal_xlsx(school: str, j: Journal) -> bytes:
    wb = Workbook()
    wb.remove(wb.worksheets[0])  # the default empty sheet
    add_journal(wb, school, j)
    return _save(wb)


def add_journal(wb: Workbook, school: str, j: Journal) -> None:
    """The journal, account totals and chart of accounts sheets."""
    period = f"{_day(j.start)} to {_day(j.end)}"
    sheet = wb.create_sheet("Journal")
    _sheet(
        sheet,
        school=school,
        title="General journal",
        subtitle=period,
        headers=JOURNAL_HEADERS,
        rows=[
            [
                e.day,
                e.number,
                e.reference,
                a.code,
                a.name,
                e.description,
                naira(dr) if dr else None,
                naira(cr) if cr else None,
            ]
            for e, a, dr, cr in lines(j)
        ],
        money_columns={7, 8},
        total=["Total", "", "", "", "", "", naira(j.debit_kobo), naira(j.credit_kobo)],
    )
    for (cell,) in sheet.iter_rows(min_row=6, min_col=1, max_col=1):
        if cell.value is not None and not isinstance(cell.value, str):
            cell.number_format = "dd mmm yyyy"
    _sheet(
        wb.create_sheet("Account totals"),
        school=school,
        title="Account totals for the period",
        subtitle=period,
        headers=[("Account code", 13), ("Account", 32), ("Type", 11), ("Debit (NGN)", 15), ("Credit (NGN)", 15)],
        rows=[
            [t.account.code, t.account.name, t.account.type, naira(t.debit_kobo), naira(t.credit_kobo)]
            for t in j.totals
        ],
        money_columns={4, 5},
        total=["Total", "", "", naira(j.debit_kobo), naira(j.credit_kobo)],
    )
    _sheet(
        wb.create_sheet("Chart of accounts"),
        school=school,
        title="Chart of accounts",
        subtitle="WardPay's default codes, with the school's own where it has set them",
        headers=[("Account code", 13), ("Account", 32), ("Type", 11)],
        rows=[[a.code, a.name, a.type] for a in j.accounts],
        money_columns=set(),
    )


def journal_csv(j: Journal) -> bytes:
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow([h for h, _ in JOURNAL_HEADERS])
    for e, a, dr, cr in lines(j):
        writer.writerow(
            [
                e.day.isoformat(),
                e.number,
                safe_text(e.reference),
                safe_text(a.code),
                safe_text(a.name),
                safe_text(e.description),
                f"{naira(dr):.2f}" if dr else "",
                f"{naira(cr):.2f}" if cr else "",
            ]
        )
    return out.getvalue().encode("utf-8-sig")  # the byte-order mark makes Excel read it as UTF-8


def _dates(ws: Worksheet, *columns: int) -> None:
    for col in columns:
        for (cell,) in ws.iter_rows(min_row=6, min_col=col, max_col=col):
            if cell.value is not None and not isinstance(cell.value, str):
                cell.number_format = "dd mmm yyyy"


def session_pack_xlsx(school: str, pack: SessionPack) -> bytes:
    period = f"{pack.name}  ·  {_day(pack.start)} to {_day(pack.end)}"
    money, cash = pack.money, pack.cash
    owed = sum(t.owed_kobo for t in pack.terms)
    wb = Workbook()
    wb.remove(wb.worksheets[0])  # the default empty sheet
    _sheet(
        wb.create_sheet("Summary"),
        school=school,
        title=f"End of session: {pack.name}",
        subtitle=period,
        headers=[("", 46), ("NGN", 18)],
        rows=[
            ["Term bills, after discounts", naira(sum(t.billed_kobo for t in pack.terms))],
            ["Discounts and scholarships given", naira(sum(t.discounts_kobo for t in pack.terms))],
            ["Paid against the term bills", naira(sum(t.paid_kobo for t in pack.terms))],
            ["Still owed on the term bills", naira(owed)],
            ["Owed in all, as of today (every bill, every year)", naira(sum(d.owed_kobo for d in pack.debtors))],
            ["", None],
            ["Money received in the session (net of reversals)", naira(money.income_kobo)],
            ["Approved spending in the session", naira(money.expenditure_kobo)],
            ["Surplus (deficit if negative)", naira(money.surplus_kobo)],
            ["Spending recorded but not approved", naira(money.awaiting_approval_kobo)],
            ["", None],
            ["Cash in the drawer when the session began", naira(cash.opening_kobo)],
            [
                "Cash taken (including cash awaiting approval)",
                naira(sum(d.received_kobo - d.reversed_kobo for d in cash.days)),
            ],
            ["Cash banked", naira(sum(d.banked_kobo for d in cash.days))],
            ["Cash spent from the drawer", naira(sum(d.spent_kobo for d in cash.days))],
            [
                "Cash that should be in the drawer at the end",
                naira(cash.days[-1].held_kobo if cash.days else cash.opening_kobo),
            ],
        ],
        money_columns={2},
    )
    _sheet(
        wb.create_sheet("By term"),
        school=school,
        title="Each term's bills",
        subtitle=period,
        headers=[
            ("Term", 16),
            ("Bills", 8),
            ("Charged (NGN)", 16),
            ("Discounts (NGN)", 16),
            ("Billed (NGN)", 16),
            ("Paid (NGN)", 16),
            ("Still owed (NGN)", 16),
            ("Pupils owing", 13),
        ],
        rows=[
            [
                t.label,
                t.bills,
                naira(t.charged_kobo),
                naira(t.discounts_kobo),
                naira(t.billed_kobo),
                naira(t.paid_kobo),
                naira(t.owed_kobo),
                t.pupils_owing,
            ]
            for t in pack.terms
        ],
        money_columns={3, 4, 5, 6, 7},
        total=[
            "Total",
            sum(t.bills for t in pack.terms),
            *(
                naira(sum(getattr(t, f) for t in pack.terms))
                for f in ("charged_kobo", "discounts_kobo", "billed_kobo", "paid_kobo", "owed_kobo")
            ),
            "",
        ],
    )
    _sheet(
        wb.create_sheet("Money in"),
        school=school,
        title="Money received, by how it came in",
        subtitle=period,
        headers=[("How", 36), ("Payments", 11), ("Amount (NGN)", 18)],
        rows=[[i.label, i.count, naira(i.amount_kobo)] for i in money.income],
        money_columns={3},
        total=["Total", sum(i.count for i in money.income), naira(money.income_kobo)],
    )
    _sheet(
        wb.create_sheet("Spending"),
        school=school,
        title="Approved spending, by category",
        subtitle=period,
        headers=[("Category", 36), ("Items", 11), ("Amount (NGN)", 18)],
        rows=[[e.label, e.count, naira(e.amount_kobo)] for e in money.expenditure],
        money_columns={3},
        total=["Total", sum(e.count for e in money.expenditure), naira(money.expenditure_kobo)],
    )
    debtors = wb.create_sheet("Debtors")
    _sheet(
        debtors,
        school=school,
        title="Who owes, as of today",
        subtitle=f"{len(pack.debtors)} students",
        headers=DEBTOR_HEADERS,
        rows=[_debtor_row(d) for d in pack.debtors],
        money_columns={4},
        total=["Total", "", "", naira(sum(d.owed_kobo for d in pack.debtors))],
    )
    _dates(debtors, 5, 10)
    months = wb.create_sheet("Cash book")
    _sheet(
        months,
        school=school,
        title="Cash book, month by month",
        subtitle=period,
        headers=[
            ("Month", 14),
            ("Taken (NGN)", 16),
            ("Reversed (NGN)", 16),
            ("Banked (NGN)", 16),
            ("Spent (NGN)", 16),
            ("Should be held at month end (NGN)", 30),
        ],
        rows=[
            [
                m.month,
                naira(m.received_kobo),
                naira(m.reversed_kobo),
                naira(m.banked_kobo),
                naira(m.spent_kobo),
                naira(m.held_kobo),
            ]
            for m in pack.months
        ],
        money_columns={2, 3, 4, 5, 6},
    )
    for (cell,) in months.iter_rows(min_row=6, min_col=1, max_col=1):
        if cell.value is not None and not isinstance(cell.value, str):
            cell.number_format = "mmm yyyy"
    add_journal(wb, school, pack.journal)
    return _save(wb)
