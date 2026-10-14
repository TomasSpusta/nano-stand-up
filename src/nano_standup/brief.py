"""Brief builder: a pure function from an instrument's week to the email that shows it."""

import html
from dataclasses import dataclass
from datetime import date, datetime

from nano_standup.domain import PRAGUE, Instrument, Reservation, Week


@dataclass(frozen=True)
class Brief:
    subject: str
    html: str


def build_brief(
    instrument: Instrument, week: Week, reservations: list[Reservation], built_at: datetime
) -> Brief:
    """Build one brief. `built_at` must carry a time zone; the footer shows it in Prague time."""
    if built_at.tzinfo is None:
        raise ValueError(f"built_at must carry a time zone, got {built_at.isoformat()}")
    reservations = sorted(reservations, key=lambda r: r.start)
    built_local = built_at.astimezone(PRAGUE)
    subject = f"Brief: {instrument.alias} - week {_week_span(week)}"
    body = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{_esc(subject)}</title></head>
<body style="margin:0;background:#f4f4f5;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a;line-height:1.4;">
  <div style="max-width:640px;margin:0 auto;padding:16px;">
    <div style="background:#fff;border-radius:8px;padding:20px 20px 12px;">
      <div style="font-size:13px;color:#666;">Weekly brief for {_esc(instrument.guarantor.name)}</div>
      <h1 style="font-size:20px;margin:4px 0 2px;">{_esc(instrument.name)}</h1>
      <div style="color:#666;font-size:13px;">{_esc(instrument.alias)} - Monday {_day_month(week.monday)} to Sunday {_day_month(week.sunday)} {week.sunday.year}</div>
      {_counts_html(reservations)}
      {_days_html(week, reservations)}
    </div>
    <div style="color:#777;font-size:12px;padding:12px 4px;">
      Built on {built_local:%A} {_day_month(built_local.date())} {built_local.year} at {built_local:%H:%M}.
      Check the booking system for later changes.
    </div>
  </div>
</body></html>"""
    return Brief(subject=subject, html=body)


def _counts_html(reservations: list[Reservation]) -> str:
    hours = sum((r.end - r.start).total_seconds() for r in reservations) / 3600
    # Reservations with no user id still count each user name once.
    users = len({r.user_id or r.user_name for r in reservations})
    without_experiment_plan = sum(1 for r in reservations if not r.experiment_plan.strip())
    tiles = [
        (str(len(reservations)), _plural(len(reservations), "reservation")),
        (f"{_number(hours)} h", "booked"),
        (str(users), _plural(users, "user")),
        (str(without_experiment_plan), "without an experiment plan"),
    ]
    # Inline blocks wrap onto a new line on a narrow screen. Outlook shows them inline, which still reads well.
    boxes = "".join(
        f'<span style="display:inline-block;background:#f4f4f5;border-radius:6px;padding:8px 12px;margin:0 8px 8px 0;">'
        f'<strong style="font-size:18px;">{value}</strong> <span style="color:#555;font-size:13px;">{label}</span></span>'
        for value, label in tiles
    )
    return f'<div style="margin:16px 0 8px;">{boxes}</div>'


def _days_html(week: Week, reservations: list[Reservation]) -> str:
    sections = []
    for day in week.days():
        todays = [r for r in reservations if r.start.date() == day]
        if not todays:
            continue
        rows = "".join(_row_html(r) for r in todays)
        sections.append(f"""
      <div style="border-top:1px solid #e5e5e5;padding-top:10px;margin-top:4px;">
        <div style="font-weight:700;font-size:15px;">{day:%A} {_day_month(day)}</div>
        <table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse;">{rows}</table>
      </div>""")
    return "".join(sections)


def _row_html(reservation: Reservation) -> str:
    experiment_plan = reservation.experiment_plan.strip()
    experiment_plan_html = _esc(experiment_plan) if experiment_plan else '<span style="color:#b45309;font-style:italic;">No experiment plan given</span>'
    return f"""
          <tr>
            <td style="width:96px;padding:10px 12px 10px 0;vertical-align:top;white-space:nowrap;"><strong>{reservation.start:%H:%M}-{reservation.end:%H:%M}</strong></td>
            <td style="padding:10px 0;vertical-align:top;">
              <div><strong>{_esc(reservation.user_name)}</strong></div>
              <div style="color:#777;font-size:12px;">{_esc(reservation.project_code)}</div>
              <div style="margin-top:4px;">{experiment_plan_html}</div>
            </td>
          </tr>"""


def _plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"


def _number(value: float) -> str:
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


def _esc(text: str) -> str:
    return html.escape(text).replace("\n", "<br>")
