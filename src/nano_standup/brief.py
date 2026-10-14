"""Brief builder: a pure function from an instrument's week to the email that shows it, as HTML and as plain text.

Both versions are drawn from the same facts (counts, days, rows), so they always agree.
The HTML uses only tables, inline styles, and simple blocks, because Outlook on Windows draws email with Word
and ignores flex, grid, and most of CSS.
"""

import html
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from nano_standup.domain import PRAGUE, Instrument, Reservation, ReservationStatus, Week

NO_EXPERIMENT_PLAN = "No experiment plan given"
FOOTER_REMINDER = "Check the booking system for later changes."
# Saturday and Sunday, as date.weekday() gives them.
WEEKEND = {5, 6}
MARKS = {ReservationStatus.CANCELLED: "Cancelled", ReservationStatus.TENTATIVE: "Tentative"}


@dataclass(frozen=True)
class Brief:
    subject: str
    html: str
    text: str


@dataclass(frozen=True)
class _Row:
    """One reservation as the brief shows it. Empty strings are left out."""

    status: ReservationStatus
    time_span: str
    length: str
    user_name: str
    project_code: str
    experiment_plan: str
    note: str
    private_note: str

    @property
    def mark(self) -> str:
        return MARKS.get(self.status, "")


@dataclass(frozen=True)
class _Day:
    day: date
    rows: list[_Row]

    @property
    def heading(self) -> str:
        return f"{self.day:%A} {_day_month(self.day)}"


def build_brief(
    instrument: Instrument, week: Week, reservations: list[Reservation], built_at: datetime
) -> Brief:
    """Build one brief. `built_at` must carry a time zone; the footer shows it in Prague time."""
    if built_at.tzinfo is None:
        raise ValueError(f"built_at must carry a time zone, got {built_at.isoformat()}")
    reservations = sorted(reservations, key=lambda r: r.start)
    built_local = built_at.astimezone(PRAGUE)
    subject = f"Brief: {instrument.alias} - week {_week_span(week)}"
    header = [
        f"Weekly brief for {instrument.guarantor.name}",
        instrument.name,
        f"{instrument.alias} - Monday {_day_month(week.monday)} to Sunday {_day_month(week.sunday)} {week.sunday.year}",
    ]
    built = f"Built on {built_local:%A} {_day_month(built_local.date())} {built_local.year} at {built_local:%H:%M}."
    # A week with no reservations at all gets a short brief: no counts and no days.
    counts = _counts(reservations) if reservations else []
    days = _days(week, reservations) if reservations else []
    return Brief(
        subject=subject,
        html=_html(subject, header, counts, days, built),
        text=_text(header, counts, days, built),
    )


def _counts(reservations: list[Reservation]) -> list[tuple[str, str]]:
    """(value, label) pairs. Cancelled reservations count only as cancelled."""
    active = [r for r in reservations if r.status is not ReservationStatus.CANCELLED]
    cancelled = len(reservations) - len(active)
    hours = sum((r.end - r.start).total_seconds() for r in active) / 3600
    # Reservations with no user id still count each user name once.
    users = len({r.user_id or r.user_name for r in active})
    without_experiment_plan = sum(1 for r in active if not r.experiment_plan.strip())
    return [
        (str(len(active)), _plural(len(active), "reservation")),
        (f"{_format_hours(hours)} h", "booked"),
        (str(users), _plural(users, "user")),
        (str(without_experiment_plan), "without an experiment plan"),
        (str(cancelled), "cancelled"),
    ]


def _days(week: Week, reservations: list[Reservation]) -> list[_Day]:
    """Every weekday, empty or not. Weekend days only when they have reservations."""
    days = []
    for day in week.days():
        rows = [_row(r) for r in reservations if r.start.date() == day]
        if rows or day.weekday() not in WEEKEND:
            days.append(_Day(day, rows))
    return days


def _row(reservation: Reservation) -> _Row:
    note = reservation.note.strip()
    return _Row(
        status=reservation.status,
        time_span=_time_span(reservation),
        length=_length(reservation),
        user_name=reservation.user_name,
        project_code=reservation.project_code,
        experiment_plan=reservation.experiment_plan.strip(),
        note=note if _adds_to(note, reservation.experiment_plan) else "",
        private_note=reservation.private_note.strip(),
    )


def _adds_to(note: str, experiment_plan: str) -> bool:
    """Users often copy the experiment plan into the note. The note adds something only when it holds more."""
    note = _comparable(note)
    return bool(note) and note not in _comparable(experiment_plan)


def _comparable(text: str) -> str:
    return " ".join(text.casefold().split())


def _time_span(reservation: Reservation) -> str:
    """"09:00 to 11:00". An end on a later day shows the day, "15:00 to Tue 13:00", and the date too when a week or more later."""
    start, end = reservation.start, reservation.end
    if end.date() == start.date():
        end_text = f"{end:%H:%M}"
    elif end.date() - start.date() < timedelta(days=7):
        end_text = f"{end:%a %H:%M}"
    else:
        end_text = f"{end:%a} {_day_month(end.date())} {end:%H:%M}"
    return f"{start:%H:%M} to {end_text}"


def _length(reservation: Reservation) -> str:
    """Under an hour in minutes, "15 min"; longer in hours, "3.5 h"."""
    minutes = round((reservation.end - reservation.start).total_seconds() / 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{_format_hours(minutes / 60)} h"


# Plain text


def _text(header: list[str], counts: list[tuple[str, str]], days: list[_Day], built: str) -> str:
    lines = [*header, ""]
    if counts:
        lines += [", ".join(f"{value} {label}" for value, label in counts), ""]
    else:
        lines += ["No reservations this week.", ""]
    for day in days:
        lines.append(day.heading.upper())
        lines += [line for row in day.rows for line in _row_text(row)] or ["  No reservations"]
        lines.append("")
    lines += [built, FOOTER_REMINDER]
    return "\n".join(lines) + "\n"


def _row_text(row: _Row) -> list[str]:
    mark = f"[{row.mark}] " if row.mark else ""
    lines = [
        f"  {mark}{row.time_span} ({row.length})",
        f"    {', '.join(part for part in (row.user_name, row.project_code) if part)}",
        *_indented(row.experiment_plan or NO_EXPERIMENT_PLAN),
    ]
    if row.note:
        lines += _indented(f"Note: {row.note}")
    if row.private_note:
        lines += _indented(f"Private note: {row.private_note}")
    return lines


def _indented(text: str) -> list[str]:
    return [f"    {line}" for line in text.splitlines()]


# HTML


def _html(subject: str, header: list[str], counts: list[tuple[str, str]], days: list[_Day], built: str) -> str:
    for_whom, name, week_line = (_html_text(line) for line in header)
    body = _counts_html(counts) + "".join(_day_html(day) for day in days) if counts else (
        '<p style="margin:16px 0 8px;">No reservations this week.</p>'
    )
    # Outlook ignores max-width, so it gets a fixed-width table instead.
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{_html_text(subject)}</title></head>
<body style="margin:0;background:#f4f4f5;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a;line-height:1.4;">
  <!--[if mso]><table role="presentation" width="640" align="center" cellspacing="0" cellpadding="0"><tr><td><![endif]-->
  <div style="max-width:640px;margin:0 auto;padding:16px;">
    <div style="background:#ffffff;border-radius:8px;padding:20px 20px 12px;">
      <div style="font-size:13px;color:#666666;">{for_whom}</div>
      <h1 style="font-size:20px;margin:4px 0 2px;">{name}</h1>
      <div style="color:#666666;font-size:13px;">{week_line}</div>
      {body}
    </div>
    <div style="color:#777777;font-size:12px;padding:12px 4px;">
      {built}
      {FOOTER_REMINDER}
    </div>
  </div>
  <!--[if mso]></td></tr></table><![endif]-->
</body></html>"""


def _counts_html(counts: list[tuple[str, str]]) -> str:
    # Inline blocks wrap onto a new line on a narrow screen. Outlook shows them inline, which still reads well.
    boxes = "".join(
        f'<span style="display:inline-block;background:#f4f4f5;border-radius:6px;padding:8px 12px;margin:0 8px 8px 0;">'
        f'<strong style="font-size:18px;">{value}</strong> <span style="color:#555555;font-size:13px;">{label}</span></span>'
        for value, label in counts
    )
    return f'<div style="margin:16px 0 8px;">{boxes}</div>'


def _day_html(day: _Day) -> str:
    rows = "".join(_row_html(row) for row in day.rows) or (
        '<tr><td style="padding:8px 0;color:#888888;">No reservations</td></tr>'
    )
    return f"""
      <div style="border-top:1px solid #e5e5e5;padding-top:10px;margin-top:4px;">
        <div style="font-weight:700;font-size:15px;">{day.heading}</div>
        <table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse;">{rows}</table>
      </div>"""


def _row_html(row: _Row) -> str:
    cancelled = row.status is ReservationStatus.CANCELLED
    # The mark itself is not crossed out, so it stays easy to read.
    crossed = "text-decoration:line-through;color:#888888;" if cancelled else ""
    # A narrow column may break the time span only after "to", never inside "Wed 13:00".
    start, end = row.time_span.split(" to ")
    time_span = f"{start.replace(' ', '&nbsp;')} to {end.replace(' ', '&nbsp;')}"
    mark_color = "#b91c1c" if cancelled else "#b45309"
    mark = (
        f'<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;color:{mark_color};">{row.mark}</div>'
        if row.mark
        else ""
    )
    experiment_plan = (
        _html_text(row.experiment_plan)
        if row.experiment_plan
        else f'<span style="color:#b45309;font-style:italic;">{NO_EXPERIMENT_PLAN}</span>'
    )
    return f"""
          <tr>
            <td style="width:112px;padding:10px 12px 10px 0;vertical-align:top;">
              <div style="font-weight:700;{crossed}">{time_span}</div>
              <div style="color:#777777;font-size:12px;">{row.length}</div>
            </td>
            <td style="padding:10px 0;vertical-align:top;">
              {mark}<div style="font-weight:700;{crossed}">{_html_text(row.user_name)}</div>
              <div style="color:#777777;font-size:12px;{crossed}">{_html_text(row.project_code)}</div>
              <div style="margin-top:4px;{crossed}">{experiment_plan}</div>
              {_labelled_html("Note", row.note)}
              {_labelled_html("Private note", row.private_note)}
            </td>
          </tr>"""


def _labelled_html(label: str, text: str) -> str:
    """A labelled line, or nothing when the text is empty."""
    if not text:
        return ""
    return f'<div style="margin-top:4px;color:#444444;font-size:13px;"><strong>{label}:</strong> {_html_text(text)}</div>'


# Shared formatting


def _plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"


def _format_hours(value: float) -> str:
    """One decimal place, dropped when the value is whole: "6.5", "3"."""
    return f"{value:.1f}".removesuffix(".0")


def _week_span(week: Week) -> str:
    """"19.-25. 10. 2026", or "28. 9.-4. 10. 2026" when the week spans two months."""
    monday, sunday = week.monday, week.sunday
    if monday.year != sunday.year:
        return f"{_day_month(monday)} {monday.year}-{_day_month(sunday)} {sunday.year}"
    if monday.month != sunday.month:
        return f"{_day_month(monday)}-{_day_month(sunday)} {sunday.year}"
    return f"{monday.day}.-{_day_month(sunday)} {sunday.year}"


def _day_month(day: date) -> str:
    return f"{day.day}. {day.month}."


def _html_text(text: str) -> str:
    """Text made safe for HTML, with line breaks kept."""
    return html.escape(text).replace("\n", "<br>")
