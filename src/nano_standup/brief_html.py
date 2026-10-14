"""The HTML version of a brief.

It uses only tables, inline styles, and simple blocks, because Outlook on Windows draws email with Word
and ignores flex, grid, and most of CSS.
"""

import html
from dataclasses import dataclass

from nano_standup.brief_content import (
    FOOTER_REMINDER,
    NO_EXPERIMENT_PLAN,
    NO_RESERVATIONS_THIS_DAY,
    NO_RESERVATIONS_THIS_WEEK,
    BriefContent,
    Count,
    Day,
    Row,
)
from nano_standup.domain import ReservationStatus

PAGE_BACKGROUND = "#f4f4f5"
GREY_TEXT = "#666666"
FADED_TEXT = "#888888"
NOTE_TEXT = "#444444"
WARNING_TEXT = "#b45309"
WARNING_BACKGROUND = "#fffbeb"
DANGER_TEXT = "#b91c1c"


@dataclass(frozen=True)
class _StatusStyle:
    mark_color: str = ""
    crossed_out: bool = False
    # Asks the guarantor to act, so the whole row stands out.
    highlighted: bool = False


STATUS_STYLES = {
    ReservationStatus.RESERVED: _StatusStyle(),
    ReservationStatus.REQUESTED: _StatusStyle(mark_color=WARNING_TEXT, highlighted=True),
    ReservationStatus.CANCELLED: _StatusStyle(mark_color=DANGER_TEXT, crossed_out=True),
}


def brief_html(content: BriefContent) -> str:
    if content.has_reservations:
        body = _counts_html(content.counts) + "".join(_day_html(day) for day in content.days)
    else:
        body = f'<p style="margin:16px 0 8px;">{NO_RESERVATIONS_THIS_WEEK}</p>'

    # Outlook ignores max-width, so it gets a fixed-width table instead.
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{_html_text(content.subject)}</title></head>
<body style="margin:0;background:{PAGE_BACKGROUND};font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a;line-height:1.4;">
  <!--[if mso]><table role="presentation" width="640" align="center" cellspacing="0" cellpadding="0"><tr><td><![endif]-->
  <div style="max-width:640px;margin:0 auto;padding:16px;">
    <div style="background:#ffffff;border-radius:8px;padding:20px 20px 12px;">
      <div style="font-size:13px;color:{GREY_TEXT};">{_html_text(content.for_whom)}</div>
      <h1 style="font-size:20px;margin:4px 0 2px;">{_html_text(content.instrument_name)}</h1>
      <div style="color:{GREY_TEXT};font-size:13px;">{_html_text(content.week_line)}</div>
      {body}
    </div>
    <div style="color:{GREY_TEXT};font-size:12px;padding:12px 4px;">
      {content.built}
      {FOOTER_REMINDER}
    </div>
  </div>
  <!--[if mso]></td></tr></table><![endif]-->
</body></html>"""


def _counts_html(counts: list[Count]) -> str:
    # Inline blocks wrap onto a new line on a narrow screen. Outlook shows them inline, which still reads well.
    return f'<div style="margin:16px 0 8px;">{"".join(_count_html(count) for count in counts)}</div>'


def _count_html(count: Count) -> str:
    background, value_color, border = (
        (WARNING_BACKGROUND, WARNING_TEXT, f"border:1px solid {WARNING_TEXT};")
        if count.needs_action
        else (PAGE_BACKGROUND, "inherit", "")
    )
    return (
        f'<span style="display:inline-block;background:{background};{border}border-radius:6px;padding:8px 12px;margin:0 8px 8px 0;">'
        f'<strong style="font-size:18px;color:{value_color};">{count.value}</strong> '
        f'<span style="color:{GREY_TEXT};font-size:13px;">{count.label}</span></span>'
    )


def _day_html(day: Day) -> str:
    rows = "".join(_row_html(row) for row in day.rows) or (
        f'<tr><td style="padding:8px 0;color:{FADED_TEXT};">{NO_RESERVATIONS_THIS_DAY}</td></tr>'
    )
    return f"""
      <div style="border-top:1px solid #e5e5e5;padding-top:10px;margin-top:4px;">
        <div style="font-weight:700;font-size:15px;">{day.heading}</div>
        <table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse;">{rows}</table>
      </div>"""


def _row_html(row: Row) -> str:
    style = STATUS_STYLES[row.status]
    # The mark itself is not crossed out, so it stays easy to read.
    crossed = f"text-decoration:line-through;color:{FADED_TEXT};" if style.crossed_out else ""
    # A background with a coloured left edge; Outlook draws both. The white top edge keeps two highlighted rows apart.
    highlight = f"background:{WARNING_BACKGROUND};border-top:4px solid #ffffff;"
    time_cell_highlight, text_cell_highlight = (
        (
            f"{highlight}border-left:3px solid {WARNING_TEXT};padding-left:8px;",
            f"{highlight}padding-right:8px;",
        )
        if style.highlighted
        else ("", "")
    )
    # A narrow column may break the time span only after "to", never inside "Wed 13:00".
    time_span = f"{row.start} to {row.end.replace(' ', '&nbsp;')}"
    mark = (
        f'<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;color:{style.mark_color};">{row.mark}</div>'
        if row.mark
        else ""
    )
    experiment_plan = (
        _html_text(row.experiment_plan)
        if row.experiment_plan
        else f'<span style="color:{WARNING_TEXT};font-style:italic;">{NO_EXPERIMENT_PLAN}</span>'
    )
    return f"""
          <tr>
            <td style="width:112px;padding:10px 12px 10px 0;vertical-align:top;{time_cell_highlight}">
              <div style="font-weight:700;{crossed}">{time_span}</div>
              <div style="color:{GREY_TEXT};font-size:12px;">{row.length}</div>
            </td>
            <td style="padding:10px 0;vertical-align:top;{text_cell_highlight}">
              {mark}<div style="font-weight:700;{crossed}">{_html_text(row.user_name)}</div>
              <div style="color:{GREY_TEXT};font-size:12px;{crossed}">{_html_text(row.project_code)}</div>
              <div style="margin-top:4px;{crossed}">{experiment_plan}</div>
              {_labelled_html("Note", row.note)}
              {_labelled_html("Private note", row.private_note)}
            </td>
          </tr>"""


def _labelled_html(label: str, text: str) -> str:
    """A labelled line, or nothing when the text is empty."""
    if not text:
        return ""
    return f'<div style="margin-top:4px;color:{NOTE_TEXT};font-size:13px;"><strong>{label}:</strong> {_html_text(text)}</div>'


def _html_text(text: str) -> str:
    """Text made safe for HTML, with line breaks kept."""
    return html.escape(text).replace("\n", "<br>")
