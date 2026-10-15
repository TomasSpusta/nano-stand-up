"""How briefs, logs, and error reports write days and counts. English on any server time language."""

from datetime import date

# Day names written out, because `%A` and `%a` follow the server's time language.
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def day_name(day: date) -> str:
    """English day name, such as "Tuesday"."""
    return DAY_NAMES[day.weekday()]


def day_month(day: date) -> str:
    """A day as "20. 10."."""
    return f"{day.day}. {day.month}."


def day_month_year(day: date) -> str:
    """A day with its year, such as "20. 10. 2026"."""
    return f"{day_month(day)} {day.year}"


def plural(count: int, word: str) -> str:
    """The word for the count: "brief" for 1, "briefs" otherwise."""
    return word if count == 1 else f"{word}s"


def counted(count: int, word: str) -> str:
    """The count with its word, such as "1 brief" or "3 briefs"."""
    return f"{count} {plural(count, word)}"
