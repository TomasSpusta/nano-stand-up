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
    HeadsUp,
    HeadsUpItem,
    LabelledNote,
    Row,
)
from nano_standup.domain import ReservationStatus

PAGE_BACKGROUND = "#f4f4f5"
GREY_TEXT = "#666666"
FADED_TEXT = "#888888"
NOTE_TEXT = "#444444"
AMBER_TEXT = "#b45309"
AMBER_BACKGROUND = "#fffbeb"
RED_TEXT = "#b91c1c"
# A cancelled reservation's text. Its colour replaces the block's own colour.
# The mark is not crossed out, so it stays easy to read.
CROSSED_OUT = f"text-decoration:line-through;color:{FADED_TEXT};"
HEAD_META = '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
BODY_STYLE = (
    f"margin:0;background:{PAGE_BACKGROUND};"
    "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a;line-height:1.4;"
)
# A table that only lays things out; screen readers skip it.
LAYOUT_TABLE = 'role="presentation" cellspacing="0" cellpadding="0"'
OUTLOOK_WRAPPER_START = f'<!--[if mso]><table {LAYOUT_TABLE} width="640" align="center"><tr><td><![endif]-->'
OUTLOOK_WRAPPER_END = "<!--[if mso]></td></tr></table><![endif]-->"


@dataclass(frozen=True)
class _StatusStyle:
    mark_color: str = ""
    crossed_out: bool = False
    # Asks the guarantor to act, so the whole row stands out.
    highlighted: bool = False


STATUS_STYLES = {
    ReservationStatus.RESERVED: _StatusStyle(),
    ReservationStatus.REQUESTED: _StatusStyle(mark_color=AMBER_TEXT, highlighted=True),
    ReservationStatus.CANCELLED: _StatusStyle(mark_color=RED_TEXT, crossed_out=True),
}


def brief_html(content: BriefContent) -> str:
    if content.has_reservations:
        body = _counts_html(content.counts) + "".join(_day_html(day) for day in content.days)
    else:
        body = f'<p style="margin:16px 0 8px;">{NO_RESERVATIONS_THIS_WEEK}</p>'
    body = _heads_ups_html(content.heads_ups) + body

    # Outlook ignores max-width, so it gets a fixed-width table instead.
    return f"""<!doctype html>
<html><head>{HEAD_META}<title>{_html_text(content.subject)}</title></head>
<body style="{BODY_STYLE}">
  {OUTLOOK_WRAPPER_START}
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
  {OUTLOOK_WRAPPER_END}
</body></html>"""


def _counts_html(counts: list[Count]) -> str:
    # Inline blocks wrap onto a new line on a narrow screen. Outlook shows them inline, which still reads well.
    return f'<div style="margin:16px 0 8px;">{"".join(_count_html(count) for count in counts)}</div>'


def _count_html(count: Count) -> str:
    background, value_color, border = (
        (AMBER_BACKGROUND, AMBER_TEXT, f"border:1px solid {AMBER_TEXT};")
        if count.needs_action
        else (PAGE_BACKGROUND, "inherit", "")
    )
    return (
        f'<span style="display:inline-block;background:{background};{border}'
        'border-radius:6px;padding:8px 12px;margin:0 8px 8px 0;">'
        f'<strong style="font-size:18px;color:{value_color};">{count.value}</strong> '
        f'<span style="color:{GREY_TEXT};font-size:13px;">{count.label}</span></span>'
    )


def _heads_ups_html(heads_ups: list[HeadsUp]) -> str:
    """The Heads-up section, or nothing when no rule matches."""
    if not heads_ups:
        return ""
    sections = "".join(_heads_up_html(heads_up) for heads_up in heads_ups)
    return (
        f'<div style="background:{AMBER_BACKGROUND};border-left:3px solid {AMBER_TEXT};'
        'border-radius:6px;padding:10px 12px;margin:16px 0 0;font-size:14px;">'
        f'<div style="font-weight:700;color:{AMBER_TEXT};">Heads-up</div>{sections}</div>'
    )


def _heads_up_html(heads_up: HeadsUp) -> str:
    """One rule's title and the reservations it matched."""
    items = "".join(_heads_up_item_html(item) for item in heads_up.items)
    return (
        f'<div style="margin-top:8px;"><strong>{html.escape(heads_up.title)}</strong>'
        f'<ul style="margin:2px 0 0;padding-left:20px;">{items}</ul></div>'
    )


def _heads_up_item_html(item: HeadsUpItem) -> str:
    # A narrow screen may break the line between the day, the time span, and the user, never inside "Tue 20. 10.".
    return f"<li>{_unbroken(item.day)} {item.span.text(_unbroken)}, {_html_text(item.user_name)}</li>"


def _day_html(day: Day) -> str:
    rows = "".join(_row_html(row) for row in day.rows) or (
        f'<tr><td style="padding:8px 0;color:{FADED_TEXT};">{NO_RESERVATIONS_THIS_DAY}</td></tr>'
    )
    return f"""
      <div style="border-top:1px solid #e5e5e5;padding-top:10px;margin-top:4px;">
        <div style="font-weight:700;font-size:15px;">{day.heading}</div>
        <table {LAYOUT_TABLE} style="width:100%;border-collapse:collapse;">{rows}</table>
      </div>"""


def _row_html(row: Row) -> str:
    style = STATUS_STYLES[row.status]
    # A background with a coloured left edge; Outlook draws both. The white top edge keeps two highlighted rows apart.
    highlight = f"background:{AMBER_BACKGROUND};border-top:4px solid #ffffff;"
    time_cell_highlight, text_cell_highlight = (
        (
            f"{highlight}border-left:3px solid {AMBER_TEXT};padding-left:8px;",
            f"{highlight}padding-right:8px;",
        )
        if style.highlighted
        else ("", "")
    )
    # A narrow column may break the time span only around "from" and "to", never inside "Wed 13:00".
    span = row.span.text(_unbroken)
    mark = (
        '<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.04em;'
        f'color:{style.mark_color};">{row.mark}</div>'
        if row.mark
        else ""
    )
    experiment_plan = (
        _html_text(row.experiment_plan)
        if row.experiment_plan
        else f'<span style="color:{AMBER_TEXT};font-style:italic;">{NO_EXPERIMENT_PLAN}</span>'
    )
    notes = "\n              ".join(_note_html(note) for note in row.notes)
    return f"""
          <tr>
            <td style="width:112px;padding:10px 12px 10px 0;vertical-align:top;{time_cell_highlight}">
              {_div("font-weight:700;", span, style.crossed_out)}
              <div style="color:{GREY_TEXT};font-size:12px;">{row.length}</div>
            </td>
            <td style="padding:10px 0;vertical-align:top;{text_cell_highlight}">
              {mark}{_div("font-weight:700;", _html_text(row.user_name), style.crossed_out)}
              {_div(f"color:{GREY_TEXT};font-size:12px;", _html_text(row.project_code), style.crossed_out)}
              {_div("margin-top:4px;", experiment_plan, style.crossed_out)}
              {notes}
            </td>
          </tr>"""


def _div(style: str, inner_html: str, crossed_out: bool) -> str:
    """A block with the given inline style, crossed out when the reservation is cancelled."""
    return f'<div style="{style}{CROSSED_OUT if crossed_out else ""}">{inner_html}</div>'


def _note_html(note: LabelledNote) -> str:
    """A labelled line, or nothing when the note is empty."""
    if not note.text:
        return ""
    return (
        f'<div style="margin-top:4px;color:{NOTE_TEXT};font-size:13px;">'
        f"<strong>{note.label}:</strong> {_html_text(note.text)}</div>"
    )


def _unbroken(text: str) -> str:
    """Text the brief makes, such as "Wed 13:00", kept on one line."""
    return text.replace(" ", "&nbsp;")


def _html_text(text: str) -> str:
    """Text made safe for HTML, with line breaks kept."""
    return html.escape(text).replace("\n", "<br>")
