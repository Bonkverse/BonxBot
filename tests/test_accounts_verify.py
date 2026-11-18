"""One pass of the friend-request check (features/accounts/verify.py), with a fake
bonk.io and a fake Discord, on real Postgres (TEST_DATABASE_URL)."""

from datetime import datetime, timedelta, timezone

import pytest

from features.accounts import service, verify
from features.accounts.matching import DATE_FORMAT, DATE_ZONE
from services import bonk_api
from tests.test_accounts_service import TULIP, URL, _sql, fresh_db, run  # noqa: F401

pytestmark = pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL to run database tests")


def _bonk_date(moment):
    return moment.astimezone(DATE_ZONE).strftime(DATE_FORMAT)


class FakeBonk:
    def __init__(self, requests=(), first_error=None):
        self.requests = list(requests)
        self.first_error = first_error
        self.logins = 0
        self.deleted = []

    async def login(self, username, password):
        self.logins += 1
        return bonk_api.LoginResult(token=f"token-{self.logins}")

    async def friend_requests(self, token):
        if self.first_error:
            error, self.first_error = self.first_error, None
            return bonk_api.RequestsResult(error=error)
        return bonk_api.RequestsResult(requests=tuple(self.requests))

    async def delete_request(self, token, bonk_id):
        self.deleted.append(bonk_id)


class FakeUser:
    def __init__(self, sent):
        self.sent = sent

    async def send(self, embed):
        self.sent.append((embed.title, embed.description))


class FakeBot:
    def __init__(self):
        self.sent = []

    def get_user(self, discord_id):
        return FakeUser(self.sent)


@pytest.fixture
def bonk(monkeypatch):
    monkeypatch.setenv("BONK_BOT_USERNAME", "BonkverseBot")
    monkeypatch.setenv("BONK_BOT_PASSWORD", "not-a-real-password")

    def install(fake):
        monkeypatch.setattr(verify, "bonk_api", fake)
        return fake

    return install


def test_friend_request_links_the_account_and_is_deleted(bonk):
    async def go():
        await service.ensure_accounts_schema()
        await service.start_claim(TULIP, "the saucy tulip")
        sent = datetime.now(timezone.utc) + timedelta(seconds=5)
        fake = bonk(FakeBonk([bonk_api.FriendRequest(777, "The Saucy Tulip", _bonk_date(sent))]))
        bot = FakeBot()

        pause = await verify._check_once(bot, verify._Session())

        assert pause == verify.POLL_SECONDS
        assert fake.deleted == [777]
        assert bot.sent == [
            ("✅ Account linked", "**The Saucy Tulip** is linked as your main account. See `/accounts`.")
        ]
        assert await service.main_account(TULIP) == "The Saucy Tulip"

    run(go())


def test_expired_token_logs_in_again_once(bonk):
    async def go():
        await service.ensure_accounts_schema()
        await service.start_claim(TULIP, "Somepoop")
        fake = bonk(FakeBonk(first_error="token"))
        session = verify._Session()
        session.token = "old"

        await verify._check_once(FakeBot(), session)

        assert fake.logins == 1 and session.token == "token-1"

    run(go())


def test_rate_limit_pauses_longer(bonk):
    async def go():
        await service.ensure_accounts_schema()
        await service.start_claim(TULIP, "Somepoop")
        bonk(FakeBonk(first_error="ratelimited"))

        pause = await verify._check_once(FakeBot(), verify._Session())

        assert pause == verify.RATE_LIMIT_PAUSE_SECONDS

    run(go())


def test_nothing_pending_means_no_bonk_calls(bonk):
    async def go():
        await service.ensure_accounts_schema()
        fake = bonk(FakeBonk())

        await verify._check_once(FakeBot(), verify._Session())

        assert fake.logins == 0

    run(go())


def test_expired_claim_is_dmed(bonk):
    async def go():
        await service.ensure_accounts_schema()
        await service.start_claim(TULIP, "Somepoop")
        await _sql("UPDATE link_claims SET expires_at = NOW() - INTERVAL '1 second'")
        bonk(FakeBonk())
        bot = FakeBot()

        await verify._check_once(bot, verify._Session())

        assert bot.sent == [
            (
                "⌛ Link timed out",
                "No friend request from **Somepoop** arrived in time. Run `/link` again to retry.",
            )
        ]

    run(go())


def test_missing_bot_login_does_nothing(bonk, monkeypatch):
    async def go():
        await service.ensure_accounts_schema()
        await service.start_claim(TULIP, "Somepoop")
        fake = bonk(FakeBonk())
        monkeypatch.delenv("BONK_BOT_PASSWORD")

        await verify._check_once(FakeBot(), verify._Session())

        assert fake.logins == 0

    run(go())
