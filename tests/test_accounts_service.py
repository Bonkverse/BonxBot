"""The account-link tables and rules (features/accounts/service.py), on real Postgres.

Needs TEST_DATABASE_URL pointing at a database these tests may wipe; skipped
otherwise. Each test starts from an empty schema.
"""

import asyncio
import os

import asyncpg
import pytest

import db.pool
from features.accounts import service
from features.accounts.matching import Match

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL to run database tests")

TULIP, OTHER = 1009876543210987654, 222222222222222222


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def fresh_db():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def setup():
        conn = await asyncpg.connect(URL)
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        await conn.close()
        db.pool.db_pool = await asyncpg.create_pool(URL)

    loop.run_until_complete(setup())
    yield
    loop.run_until_complete(db.pool.db_pool.close())
    loop.close()


async def _sql(query, *args):
    async with db.pool.db_pool.acquire() as conn:
        return await conn.fetch(query, *args)


async def _levelbot_table(rows):
    """LevelBot's table as it was copied: Discord ids as text, no new columns."""
    await _sql(
        "CREATE TABLE discord_bonk_accounts (discord_user_id TEXT NOT NULL,"
        " bonk_username TEXT NOT NULL, is_main BOOLEAN DEFAULT FALSE,"
        " created_at TIMESTAMP DEFAULT NOW(), UNIQUE (discord_user_id, bonk_username))"
    )
    for discord_id, name, main in rows:
        await _sql(
            "INSERT INTO discord_bonk_accounts VALUES ($1, $2, $3)", str(discord_id), name, main
        )


async def _match(claim_id, name, bonk_id=777):
    claims = {c.id: c for c in await service.pending_claims()}
    return Match(claims[claim_id], bonk_id, name)


# --- schema -----------------------------------------------------------------------


def test_schema_upgrades_levelbots_table_and_keeps_its_rows():
    async def go():
        await _levelbot_table([(TULIP, "The Saucy Tulip", True), (TULIP, "Tulip Alt", False)])
        await service.ensure_accounts_schema()
        await service.ensure_accounts_schema()  # safe to run again
        rows = await service.accounts_of(TULIP)
        assert [(r["bonk_username"], r["is_main"], r["source"]) for r in rows] == [
            ("The Saucy Tulip", True, "levelbot"),
            ("Tulip Alt", False, "levelbot"),
        ]
        kind = await _sql(
            "SELECT data_type FROM information_schema.columns WHERE table_name ="
            " 'discord_bonk_accounts' AND column_name = 'discord_user_id'"
        )
        assert kind[0]["data_type"] == "bigint"

    run(go())


def test_schema_on_an_empty_database():
    async def go():
        await service.ensure_accounts_schema()
        assert await service.accounts_of(TULIP) == []

    run(go())


def test_one_discord_user_per_bonk_name_ignoring_case():
    async def go():
        await service.ensure_accounts_schema()
        await _sql("INSERT INTO discord_bonk_accounts (discord_user_id, bonk_username) VALUES ($1, 'Tulip')", TULIP)
        with pytest.raises(asyncpg.UniqueViolationError):
            await _sql("INSERT INTO discord_bonk_accounts (discord_user_id, bonk_username) VALUES ($1, 'tulip')", OTHER)

    run(go())


# --- claims ---------------------------------------------------------------------------


def test_claim_then_link_makes_the_first_account_main():
    async def go():
        await service.ensure_accounts_schema()
        start = await service.start_claim(TULIP, "the saucy tulip")
        assert start.problem is None and start.minutes_left == 10

        outcome = await service.complete_claim(await _match(start.claim_id, "The Saucy Tulip"))

        assert (outcome.linked, outcome.is_main) == (True, True)
        [row] = await service.accounts_of(TULIP)
        assert (row["bonk_username"], row["bonk_id"], row["source"]) == (
            "The Saucy Tulip",  # bonk.io's spelling
            777,
            "friend_request",
        )
        assert row["verified_at"] is not None
        assert await service.pending_claims() == []
        assert await service.main_account(TULIP) == "The Saucy Tulip"

    run(go())


def test_second_account_is_an_alt():
    async def go():
        await _levelbot_table([(TULIP, "The Saucy Tulip", True)])
        await service.ensure_accounts_schema()
        start = await service.start_claim(TULIP, "Tulip Alt")

        outcome = await service.complete_claim(await _match(start.claim_id, "Tulip Alt", 8))

        assert (outcome.linked, outcome.is_main) == (True, False)
        assert await service.main_account(TULIP) == "The Saucy Tulip"

    run(go())


def test_cannot_claim_a_name_linked_to_someone_else():
    async def go():
        await _levelbot_table([(OTHER, "Somepoop", True)])
        await service.ensure_accounts_schema()

        start = await service.start_claim(TULIP, "SOMEPOOP")

        assert start.claim_id is None
        assert "linked to another Discord account" in start.problem

    run(go())


def test_claiming_your_own_linked_name_says_so():
    async def go():
        await _levelbot_table([(TULIP, "The Saucy Tulip", True)])
        await service.ensure_accounts_schema()

        start = await service.start_claim(TULIP, "the saucy tulip")

        assert "already linked to you" in start.problem

    run(go())


def test_one_pending_claim_per_name():
    async def go():
        await service.ensure_accounts_schema()
        first = await service.start_claim(TULIP, "Somepoop")

        again = await service.start_claim(TULIP, "somepoop")
        rival = await service.start_claim(OTHER, "Somepoop")

        assert again.claim_id == first.claim_id and again.problem is None
        assert "Someone else is verifying" in rival.problem

    run(go())


def test_three_tries_an_hour():
    async def go():
        await service.ensure_accounts_schema()
        for name in ("A", "B", "C"):
            assert (await service.start_claim(TULIP, name)).problem is None

        fourth = await service.start_claim(TULIP, "D")

        assert "a lot of tries" in fourth.problem

    run(go())


def test_account_limit():
    async def go():
        await _levelbot_table([(TULIP, f"Alt {i}", i == 0) for i in range(service.MAX_ACCOUNTS)])
        await service.ensure_accounts_schema()

        start = await service.start_claim(TULIP, "One More")

        assert "up to 10" in start.problem

    run(go())


def test_expired_claims_are_reported_once_and_cannot_complete():
    async def go():
        await service.ensure_accounts_schema()
        start = await service.start_claim(TULIP, "Somepoop")
        match = await _match(start.claim_id, "Somepoop")
        await _sql("UPDATE link_claims SET expires_at = NOW() - INTERVAL '1 second'")

        expired = await service.expire_claims()
        assert [(r["discord_user_id"], r["bonk_username"]) for r in expired] == [(TULIP, "Somepoop")]
        assert await service.expire_claims() == []
        assert (await service.complete_claim(match)).linked is False
        assert await service.accounts_of(TULIP) == []

    run(go())


def test_name_taken_by_someone_else_meanwhile():
    async def go():
        await service.ensure_accounts_schema()
        start = await service.start_claim(TULIP, "Somepoop")
        match = await _match(start.claim_id, "Somepoop")
        await _sql(
            "INSERT INTO discord_bonk_accounts (discord_user_id, bonk_username) VALUES ($1, 'Somepoop')",
            OTHER,
        )

        outcome = await service.complete_claim(match)

        assert outcome.linked is False and "someone else" in outcome.problem
        status = await _sql("SELECT status FROM link_claims")
        assert status[0]["status"] == "taken"

    run(go())


# --- main and unlink -------------------------------------------------------------------


def test_set_main_and_unlink():
    async def go():
        await _levelbot_table([(TULIP, "Main", True), (TULIP, "Alt One", False), (TULIP, "Alt Two", False)])
        await _sql("UPDATE discord_bonk_accounts SET created_at = NOW() - (bonk_username = 'Alt One')::int * INTERVAL '1 day'")
        await service.ensure_accounts_schema()

        assert await service.set_main(TULIP, "alt two") == "Alt Two"
        assert await service.main_account(TULIP) == "Alt Two"
        assert await service.set_main(TULIP, "Somepoop") is None

        assert await service.unlink(TULIP, "ALT TWO") == "Alt Two"
        assert await service.main_account(TULIP) == "Alt One"  # oldest left becomes main
        assert await service.unlink(OTHER, "Main") is None  # not theirs
        names = [r["bonk_username"] for r in await service.accounts_of(TULIP)]
        assert names == ["Alt One", "Main"]

    run(go())
