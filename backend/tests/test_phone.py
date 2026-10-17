"""Phone number normalisation (app/core/phone.py)."""

import pytest

from app.core.phone import InvalidPhone, mask_phone, normalize_phone


@pytest.mark.parametrize(
    "raw",
    [
        "08031234567",
        "8031234567",
        "2348031234567",
        "+2348031234567",
        "+234 803 123 4567",
        "0803-123-4567",
        "(0803) 123 4567",
        "+23408031234567",
        "002348031234567",
    ],
)
def test_nigerian_numbers_in_every_shape(raw):
    assert normalize_phone(raw) == "+2348031234567"


def test_foreign_numbers_are_kept():
    assert normalize_phone("+44 7700 900123") == "+447700900123"


@pytest.mark.parametrize("raw", ["", "12345", "0803123456", "080312345678", "phone", "+0123456789"])
def test_invalid_numbers(raw):
    with pytest.raises(InvalidPhone):
        normalize_phone(raw)


def test_mask():
    assert mask_phone("+2348031234567") == "+234 *** *** 4567"
