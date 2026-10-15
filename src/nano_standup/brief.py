"""Brief builder: a pure function from an instrument's week to the email that shows it, as HTML and as plain text.

Both versions are drawn from the same content (counts, days, rows), so they always agree.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from nano_standup.brief_content import brief_content
from nano_standup.brief_html import brief_html
from nano_standup.brief_text import brief_text
from nano_standup.domain import Instrument, Reservation, UserFacts, Week
from nano_standup.heads_ups import HeadsUpSettings


@dataclass(frozen=True)
class Brief:
    subject: str
    html: str
    text: str


def build_brief(
    instrument: Instrument,
    week: Week,
    reservations: list[Reservation],
    user_facts: Mapping[str, UserFacts],
    built_at: datetime,
    settings: HeadsUpSettings,
    unsubscribe_link: str,
) -> Brief:
    """Build one brief.

    `user_facts` by user id, for the heads-up rules about users. A user without facts has no certificate.
    `built_at` must carry a time zone; the footer shows it in Prague time.
    The footer ends with `unsubscribe_link`, which ends the subscription this brief goes to.
    """
    content = brief_content(instrument, week, reservations, user_facts, built_at, settings, unsubscribe_link)
    return Brief(subject=content.subject, html=brief_html(content), text=brief_text(content))
