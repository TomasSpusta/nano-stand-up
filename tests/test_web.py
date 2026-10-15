import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from nano_standup.booking_api import BookingApiError
from nano_standup.booking_source import BookingSource, FakeBookingSource
from nano_standup.domain import Guarantor, Instrument, InstrumentName, Reservation, Week
from nano_standup.mail import InMemoryMailSender, MailSender, Message
from nano_standup.subscription_request import CONFIRM_MAIL_GAP
from nano_standup.subscriptions import CONFIRM_LINK_LIFETIME, Subscription, SubscriptionStore
from nano_standup.web import create_app

from helpers import GUARANTOR, INSTRUMENT, visible_text

NOW = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
BASE_URL = "https://standup.example.org"
FROM = "standup@example.org"
OTHER = Instrument(
    id="instr-2",
    name="Test <Chemical> Laboratory",
    alias="TEST-LAB",
    guarantor=Guarantor(name="Ota Zkusebni", email="ota.zkusebni@example.org"),
)
CONFIRM_LINK = re.compile(re.escape(BASE_URL) + r"/confirm\?token=([A-Za-z0-9_-]+)")


class Clock:
    """A clock the test can move forward."""

    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class Site:
    def __init__(
        self, tmp_path: Path, source: BookingSource | None = None, mail_sender: MailSender | None = None
    ) -> None:
        tmp_path.mkdir(exist_ok=True)
        self.store = SubscriptionStore(tmp_path / "standup.sqlite3")
        self.mail = InMemoryMailSender()
        self.clock = Clock()
        app = create_app(
            source=source or FakeBookingSource([INSTRUMENT, OTHER]),
            store=self.store,
            mail_sender=mail_sender or self.mail,
            clock=self.clock,
            from_address=FROM,
            public_base_url=BASE_URL + "/",
        )
        self.client = TestClient(app)

    def subscribe(self, instrument_id: str, email: str) -> str:
        response = self.client.post("/", data={"instrument_id": instrument_id, "email": email})
        assert response.status_code == 200
        return str(response.text)

    def confirm_link(self) -> str:
        """The confirm link in the last mail sent, without the base URL."""
        match = CONFIRM_LINK.search(self.mail.sent[-1].text)
        assert match, self.mail.sent[-1].text
        assert match.group() in self.mail.sent[-1].html
        return match.group().removeprefix(BASE_URL)

    def press_confirm(self, link: str) -> Response:
        """What a person does: open the link, then press the button on the page."""
        token = link.partition("token=")[2]
        assert f'value="{token}"' in self.client.get(link).text
        response: Response = self.client.post("/confirm", data={"token": token})
        return response


@pytest.fixture
def site(tmp_path: Path) -> Site:
    return Site(tmp_path)


class PartlyListedSource(FakeBookingSource):
    """Knows OTHER, but leaves it out of the list, like a person resource or a retired instrument."""

    def list_subscribable_instruments(self) -> list[InstrumentName]:
        return [i for i in super().list_subscribable_instruments() if i.id != OTHER.id]


class CountingSource(FakeBookingSource):
    def __init__(self) -> None:
        super().__init__([INSTRUMENT, OTHER])
        self.list_reads = 0

    def list_subscribable_instruments(self) -> list[InstrumentName]:
        self.list_reads += 1
        return super().list_subscribable_instruments()


class DownBookingSource:
    """A booking source whose API cannot be reached."""

    def list_subscribable_instruments(self) -> list[InstrumentName]:
        raise BookingApiError("/api/equipment", None, "Connection refused")

    def get_instrument(self, instrument_id: str) -> Instrument:
        raise BookingApiError(f"/api/equipment/{instrument_id}", None, "Connection refused")

    def get_reservations(self, instrument_id: str, week: Week) -> list[Reservation]:
        raise BookingApiError("/api/service-appointment", None, "Connection refused")


class BrokenMailSender:
    def send(self, message: Message) -> None:
        raise RuntimeError("mail server said something odd")


# The subscribe page


def test_the_page_lists_the_instruments_by_alias_and_name_escaped(site: Site) -> None:
    response = site.client.get("/")

    assert response.status_code == 200
    assert f'<option value="{INSTRUMENT.id}">TEST-SEM - Test Electron Microscope</option>' in response.text
    assert f'<option value="{OTHER.id}">TEST-LAB - Test &lt;Chemical&gt; Laboratory</option>' in response.text
    assert "<Chemical>" not in response.text


def test_the_page_reads_the_instrument_list_once_while_it_is_fresh(tmp_path: Path) -> None:
    source = CountingSource()
    site = Site(tmp_path, source)

    site.client.get("/")
    site.client.get("/")
    site.subscribe(INSTRUMENT.id, GUARANTOR.email)

    assert source.list_reads == 1


def test_the_page_says_when_the_booking_system_cannot_be_reached(tmp_path: Path) -> None:
    site = Site(tmp_path, DownBookingSource())

    response = site.client.get("/")

    assert response.status_code == 503
    assert "cannot reach the booking system" in visible_text(response.text)


# Subscribing


def test_a_matching_email_gets_a_confirm_mail_at_the_booking_system_email(site: Site) -> None:
    site.subscribe(INSTRUMENT.id, "  HANA.Testova@example.ORG ")

    [message] = site.mail.sent
    assert message.to == GUARANTOR.email
    assert message.from_address == FROM
    assert "TEST-SEM" in message.subject
    assert "Test Electron Microscope" in visible_text(message.html)
    assert CONFIRM_LINK.search(message.text)
    assert site.store.list_active() == []


def test_the_confirm_mail_goes_to_the_booking_system_email_not_the_typed_text(tmp_path: Path) -> None:
    instrument = Instrument("instr-3", "Test Mill", "TEST-MILL", Guarantor("Eva Pokusna", "Eva.Pokusna@Example.org"))
    site = Site(tmp_path, FakeBookingSource([instrument]))

    site.subscribe(instrument.id, "eva.pokusna@example.org")

    assert [m.to for m in site.mail.sent] == ["Eva.Pokusna@Example.org"]


def test_the_confirm_mail_escapes_the_instrument_name(site: Site) -> None:
    site.subscribe(OTHER.id, OTHER.guarantor.email)

    [message] = site.mail.sent
    assert "Test &lt;Chemical&gt; Laboratory" in message.html
    assert "<Chemical>" not in message.html
    assert "Test <Chemical> Laboratory" in message.text


def test_a_non_matching_email_gets_no_mail_and_the_same_page(site: Site) -> None:
    matched = site.subscribe(INSTRUMENT.id, GUARANTOR.email)
    site.mail.sent.clear()

    not_matched = site.subscribe(INSTRUMENT.id, "someone.else@example.org")

    assert site.mail.sent == []
    assert not_matched == matched
    assert "confirm link" in visible_text(matched)


def test_the_guarantor_name_is_never_matched(site: Site) -> None:
    site.subscribe(INSTRUMENT.id, GUARANTOR.name)

    assert site.mail.sent == []


def test_an_unknown_instrument_gets_no_mail_and_the_same_page(site: Site) -> None:
    matched = site.subscribe(INSTRUMENT.id, GUARANTOR.email)

    unknown = site.subscribe("no-such-id", GUARANTOR.email)

    assert unknown == matched
    assert len(site.mail.sent) == 1


def test_an_instrument_left_out_of_the_list_gets_no_mail_and_the_same_page(tmp_path: Path) -> None:
    site = Site(tmp_path, PartlyListedSource([INSTRUMENT, OTHER]))
    matched = site.subscribe(INSTRUMENT.id, GUARANTOR.email)

    left_out = site.subscribe(OTHER.id, OTHER.guarantor.email)

    assert left_out == matched
    assert [m.to for m in site.mail.sent] == [GUARANTOR.email]


def test_a_second_request_within_minutes_sends_no_second_mail(site: Site) -> None:
    first = site.subscribe(INSTRUMENT.id, GUARANTOR.email)
    site.clock.now += CONFIRM_MAIL_GAP - timedelta(seconds=1)

    second = site.subscribe(INSTRUMENT.id, GUARANTOR.email)

    assert second == first
    assert len(site.mail.sent) == 1
    site.clock.now += timedelta(seconds=1)
    site.subscribe(INSTRUMENT.id, GUARANTOR.email)
    assert len(site.mail.sent) == 2


def test_a_form_without_an_email_asks_for_one(site: Site) -> None:
    response = site.client.post("/", data={"instrument_id": INSTRUMENT.id, "email": "  "})

    assert response.status_code == 400
    assert "Pick an instrument and enter your email" in visible_text(response.text)
    assert site.mail.sent == []


def test_a_request_while_the_booking_system_is_down_shows_the_same_page(tmp_path: Path) -> None:
    matched = Site(tmp_path / "up").subscribe(INSTRUMENT.id, GUARANTOR.email)
    site = Site(tmp_path / "down", DownBookingSource())

    response = site.subscribe(INSTRUMENT.id, GUARANTOR.email)

    assert response == matched
    assert site.mail.sent == []


def test_an_unexpected_mail_failure_is_logged_and_shows_the_same_page(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    matched = Site(tmp_path / "working").subscribe(INSTRUMENT.id, GUARANTOR.email)
    site = Site(tmp_path / "broken", mail_sender=BrokenMailSender())

    response = site.subscribe(INSTRUMENT.id, GUARANTOR.email)

    assert response == matched
    assert "mail server said something odd" in caplog.text
    assert f"Subscription request for instrument {INSTRUMENT.id} failed" in caplog.text


# The confirm link


def test_opening_the_confirm_link_does_not_use_it(site: Site) -> None:
    site.subscribe(INSTRUMENT.id, GUARANTOR.email)
    link = site.confirm_link()

    # A mail scanner may open the link before the guarantor does.
    first = site.client.get(link)
    second = site.client.get(link)

    assert first.status_code == second.status_code == 200
    assert "Confirm" in visible_text(second.text)
    assert '<form method="post"' in second.text
    assert site.store.list_active() == []


def test_pressing_confirm_makes_the_subscription_active(site: Site) -> None:
    site.subscribe(INSTRUMENT.id, GUARANTOR.email)

    response = site.press_confirm(site.confirm_link())

    assert response.status_code == 200
    assert "Your subscription is active" in visible_text(response.text)
    assert site.store.list_active() == [Subscription(INSTRUMENT.id, GUARANTOR.email)]


def test_a_used_confirm_link_shows_a_clear_message(site: Site) -> None:
    site.subscribe(INSTRUMENT.id, GUARANTOR.email)
    link = site.confirm_link()
    site.press_confirm(link)
    token = link.partition("token=")[2]

    opened = site.client.get(link)
    pressed = site.client.post("/confirm", data={"token": token})

    for response in (opened, pressed):
        assert response.status_code == 410
        assert "already used" in visible_text(response.text)


def test_an_expired_confirm_link_shows_a_clear_message_and_activates_nothing(site: Site) -> None:
    site.subscribe(INSTRUMENT.id, GUARANTOR.email)
    link = site.confirm_link()
    site.clock.now += CONFIRM_LINK_LIFETIME + timedelta(minutes=1)

    opened = site.client.get(link)
    pressed = site.client.post("/confirm", data={"token": link.partition("token=")[2]})

    for response in (opened, pressed):
        assert response.status_code == 410
        assert "expired" in visible_text(response.text)
    assert site.store.list_active() == []


@pytest.mark.parametrize("link", ["/confirm?token=made-up-token", "/confirm"])
def test_an_unknown_confirm_link_shows_a_clear_message(site: Site, link: str) -> None:
    response = site.client.get(link)

    assert response.status_code == 404
    assert "not valid" in visible_text(response.text)


def test_pressing_confirm_with_an_unknown_token_shows_a_clear_message(site: Site) -> None:
    response = site.client.post("/confirm", data={"token": "made-up-token"})

    assert response.status_code == 404
    assert "not valid" in visible_text(response.text)


def test_signing_up_twice_for_the_same_instrument_keeps_one_subscription(site: Site) -> None:
    site.subscribe(INSTRUMENT.id, GUARANTOR.email)
    first = site.confirm_link()
    site.clock.now += CONFIRM_MAIL_GAP
    site.subscribe(INSTRUMENT.id, GUARANTOR.email.upper())
    second = site.confirm_link()

    site.press_confirm(first)
    site.press_confirm(second)

    assert first != second
    assert site.store.list_active() == [Subscription(INSTRUMENT.id, GUARANTOR.email)]
