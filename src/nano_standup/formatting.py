"""How the brief and the log write days. English on any server time language."""

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
