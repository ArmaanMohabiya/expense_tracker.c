"""Input validation. These rules follow validate.c from the C version.
Every function either returns a clean value or raises ValidationError."""
import calendar
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

CATEGORIES = ["Food", "Transport", "Books", "Recharge", "Entertainment", "Other"]
MAX_AMOUNT = Decimal("1000000")
MAX_NOTE_LENGTH = 100


class ValidationError(Exception):
    """User input is not acceptable. `field` names the input that caused it."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.message = message
        self.field = field


def clean_username(value):
    if not isinstance(value, str):
        raise ValidationError("Username is required.", "username")
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", value):
        raise ValidationError("Username must be 3-20 letters, digits or underscores.", "username")
    return value


def clean_password(value, field="password"):
    if not isinstance(value, str):
        raise ValidationError("Password is required.", field)
    if not 8 <= len(value) <= 64:
        raise ValidationError("Password must be 8-64 characters.", field)
    return value


def clean_date(value, field="date"):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValidationError("Date must look like YYYY-MM-DD.", field)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValidationError("That is not a real calendar date.", field)
    if not 2000 <= parsed.year <= 2100:
        raise ValidationError("Year must be between 2000 and 2100.", field)
    return value


def clean_category(value):
    if isinstance(value, str):
        for name in CATEGORIES:
            if name.lower() == value.strip().lower():
                return name                       # always return the official spelling
    raise ValidationError("Category must be one of: " + ", ".join(CATEGORIES) + ".", "category")


def clean_amount(value, field="amount", label="Amount"):
    """Return the amount in paise (an integer), so money is never stored as a float."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValidationError(label + " must be a number.", field)
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValidationError(label + " must be a number.", field)
    if not amount.is_finite():
        raise ValidationError(label + " must be a number.", field)
    if amount <= 0:
        raise ValidationError(label + " must be greater than 0.", field)
    if amount > MAX_AMOUNT:
        raise ValidationError(label + " must be at most 1,000,000.", field)
    rounded = amount.quantize(Decimal("0.01"))
    if amount != rounded:
        # Floats such as 0.10000000000000003 are only computer rounding noise: accept those.
        # Anything with real extra decimals (like 1.234) is refused.
        if isinstance(value, float) and abs(amount - rounded) < Decimal("1e-9"):
            amount = rounded
        else:
            raise ValidationError(label + " can have at most 2 decimal places.", field)
    return int(amount * 100)


def clean_note(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError("Note must be text.", "note")
    note = " ".join(value.split())                # tidy spaces, tabs and newlines
    if len(note) > MAX_NOTE_LENGTH:
        raise ValidationError("Note must be at most %d characters." % MAX_NOTE_LENGTH, "note")
    if any(unicodedata.category(ch).startswith("C") for ch in note):
        raise ValidationError("Note contains invalid characters.", "note")
    return note


def clean_month(value, field="month"):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
        raise ValidationError("Month must look like YYYY-MM.", field)
    year, month = int(value[:4]), int(value[5:])
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        raise ValidationError("That is not a valid month.", field)
    return value


def month_bounds(month):
    """First and last day of 'YYYY-MM' as 'YYYY-MM-DD' strings."""
    year, mon = int(month[:4]), int(month[5:])
    return "%s-01" % month, "%s-%02d" % (month, calendar.monthrange(year, mon)[1])


def shift_month(month, delta):
    """shift_month('2026-01', -1) -> '2025-12'."""
    index = int(month[:4]) * 12 + (int(month[5:]) - 1) + delta
    return "%04d-%02d" % (index // 12, index % 12 + 1)


def clean_search(value):
    if not isinstance(value, str):
        raise ValidationError("Search text must be text.", "q")
    value = " ".join(value.split())
    if len(value) > 50:
        raise ValidationError("Search text must be at most 50 characters.", "q")
    return value


def parse_expense(data):
    """Validate the JSON body of a create or update request.
    Returns (date, category, amount_in_paise, note)."""
    return (
        clean_date(data.get("date")),
        clean_category(data.get("category")),
        clean_amount(data.get("amount")),
        clean_note(data.get("note")),
    )
