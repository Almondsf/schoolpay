"""Formatting money for people. Amounts are always integer kobo; this is only for display."""


def format_naira(kobo: int) -> str:
    """15000050 -> "₦150,000.50"; whole amounts keep two decimals ("₦150,000.00") as on receipts."""
    sign = "-" if kobo < 0 else ""
    naira, rest = divmod(abs(kobo), 100)
    return f"{sign}₦{naira:,}.{rest:02d}"
