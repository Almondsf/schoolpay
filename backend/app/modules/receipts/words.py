"""Amounts in words for receipts: 15000050 kobo -> "One hundred and fifty thousand naira, fifty kobo only"."""

_ONES = [
    "",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SCALES = [(1_000_000_000_000, "trillion"), (1_000_000_000, "billion"), (1_000_000, "million"), (1_000, "thousand")]


def _below_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{_ONES[hundreds]} hundred")
    if rest:
        words = _ONES[rest] if rest < 20 else _TENS[rest // 10] + (f"-{_ONES[rest % 10]}" if rest % 10 else "")
        parts.append(("and " if hundreds else "") + words)
    return " ".join(parts)


def number_in_words(n: int) -> str:
    if n == 0:
        return "zero"
    parts = []
    for size, name in _SCALES:
        count, n = divmod(n, size)
        if count:
            parts.append(f"{number_in_words(count)} {name}")
    if n:
        # British usage: "one thousand and five"
        parts.append(("and " if parts and n < 100 else "") + _below_thousand(n))
    return ", ".join(parts).replace(", and", " and")


def naira_in_words(kobo: int) -> str:
    naira, rest = divmod(abs(kobo), 100)
    text = f"{number_in_words(naira)} naira" if naira else ""
    if rest:
        text += (", " if text else "") + f"{number_in_words(rest)} kobo"
    text = (text or "zero naira") + " only"
    return text[0].upper() + text[1:]
