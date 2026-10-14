"""The plain-text version of a brief, for mail apps that do not show HTML."""

from nano_standup.brief_content import (
    FOOTER_REMINDER,
    NO_EXPERIMENT_PLAN,
    NO_RESERVATIONS_THIS_DAY,
    NO_RESERVATIONS_THIS_WEEK,
    BriefContent,
    Row,
)


def brief_text(content: BriefContent) -> str:
    lines = [content.for_whom, content.instrument_name, content.week_line, ""]
    if content.has_reservations:
        lines += [", ".join(f"{count.value} {count.label}" for count in content.counts), ""]
    else:
        lines += [NO_RESERVATIONS_THIS_WEEK, ""]
    for day in content.days:
        lines.append(day.heading.upper())
        lines += [line for row in day.rows for line in _row_text(row)] or [f"  {NO_RESERVATIONS_THIS_DAY}"]
        lines.append("")
    lines += [content.built, FOOTER_REMINDER]
    return "\n".join(lines) + "\n"


def _row_text(row: Row) -> list[str]:
    mark = f"[{row.mark}] " if row.mark else ""
    lines = [
        f"  {mark}{row.start} to {row.end} ({row.length})",
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
