"""A stored submission's creator_source reaches Bonkverse, on the first push and
on retries (features/skins/bonkverse_sync.py). Real Postgres (TEST_DATABASE_URL)."""

import asyncio

import pytest

from features.skins import bonkverse_sync
from services import bonkverse_client
from tests.test_accounts_service import TULIP, URL, _sql, fresh_db  # noqa: F401

pytestmark = pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL to run database tests")


def test_sync_sends_the_stored_creator_source(monkeypatch):
    calls = []

    async def fake_submit(**kwargs):
        calls.append(kwargs)
        return bonkverse_client.SubmitResult(bonkverse_skin_id=9)

    monkeypatch.setattr(bonkverse_sync.bonkverse_client, "submit_skin", fake_submit)
    monkeypatch.setattr(bonkverse_sync, "_configured", lambda: True)

    async def go():
        await _sql(
            "CREATE TABLE skins (id SERIAL PRIMARY KEY, skin_name TEXT, skin_url TEXT,"
            " final_url TEXT, skin_code TEXT, creator TEXT, submitter_id BIGINT)"
        )
        await bonkverse_sync.ensure_schema()
        await bonkverse_sync.ensure_schema()  # safe to re-run
        await _sql(
            "INSERT INTO skins (skin_name, skin_url, skin_code, creator, creator_source, submitter_id)"
            " VALUES ('Reality.exe', 'l', 'c', 'Somepoop', 'bonkleagues', $1),"
            "        ('Old One', 'l2', 'c2', 'Someone', NULL, $1)",
            TULIP,
        )
        await bonkverse_sync.sync_skin(1)
        await bonkverse_sync.sync_skin(2)

    asyncio.get_event_loop().run_until_complete(go())

    assert [(c["creator"], c["creator_source"]) for c in calls] == [
        ("Somepoop", "bonkleagues"),
        ("Someone", None),  # older rows: Bonkverse keeps them "as_entered"
    ]
