"""Wording shared by briefs, logs, and error reports: counts and dates, the same everywhere."""

from datetime import date


def plural(count: int, word: str) -> str:
    """The word for the count: "brief" for 1, "briefs" otherwise."""
    return word if count == 1 else f"{word}s"


def counted(count: int, word: str) -> str:
    """The count with its word, such as "1 brief" or "3 briefs"."""
    return f"{count} {plural(count, word)}"


def day_month(day: date) -> str:
    """A day without its year, such as "20. 10."."""
    return f"{day.day}. {day.month}."


def day_month_year(day: date) -> str:
    """A day with its year, such as "20. 10. 2026"."""
    return f"{day_month(day)} {day.year}"
