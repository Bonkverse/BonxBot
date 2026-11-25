"""Which friend request proves which /link claim (features/accounts/matching.py)."""

from datetime import datetime, timedelta, timezone

import pytest

from features.accounts.matching import (
    Claim,
    match_requests,
    name_key,
    name_problem,
    request_time,
)
from services.bonk_api import FriendRequest

UTC = timezone.utc
# 2026-11-17 21:00:00 US Eastern (EST, UTC-5) is 2026-11-18 02:00:00 UTC.
NOW = datetime(2026, 11, 18, 2, 5, tzinfo=UTC)
STARTED = datetime(2026, 11, 18, 2, 0, tzinfo=UTC)


def _claim(name="The Saucy Tulip", started=STARTED, claim_id=1, discord_id=42):
    return Claim(claim_id, discord_id, name, started)


def _request(name="The Saucy Tulip", date="2026-11-17 21:01:00", bonk_id=777):
    return FriendRequest(bonk_id, name, date)


def test_request_time_reads_bonk_dates_as_us_eastern():
    assert request_time("2026-11-17 21:00:00") == datetime(2026, 11, 18, 2, 0, tzinfo=UTC)
    assert request_time("2026-07-01 12:00:00") == datetime(2026, 7, 1, 16, 0, tzinfo=UTC)  # EDT


@pytest.mark.parametrize("date", [None, "", "yesterday", "2026-13-01 00:00:00"])
def test_unreadable_dates_are_none(date):
    assert request_time(date) is None


def test_request_after_the_claim_proves_it():
    matches, delete = match_requests([_claim()], [_request()], NOW)

    assert [(m.claim.id, m.bonk_id, m.bonk_username) for m in matches] == [
        (1, 777, "The Saucy Tulip")
    ]
    assert delete == [777]


def test_name_case_doesnt_matter_and_bonk_spelling_is_kept():
    matches, _ = match_requests([_claim("the saucy tulip")], [_request("The Saucy Tulip")], NOW)

    assert matches[0].bonk_username == "The Saucy Tulip"


def test_spaces_do_matter():
    matches, _ = match_requests([_claim(" The Saucy Tulip")], [_request("The Saucy Tulip")], NOW)

    assert matches == []


def test_request_sent_before_the_claim_proves_nothing():
    old = _request(date="2026-11-17 20:55:00")  # five minutes before the claim

    matches, _ = match_requests([_claim()], [old], NOW)

    assert matches == []


def test_small_clock_difference_is_allowed():
    early = _request(date="2026-11-17 20:59:30")  # 30 s before the claim

    matches, _ = match_requests([_claim()], [early], NOW)

    assert len(matches) == 1


def test_request_without_a_readable_date_proves_nothing_and_is_deleted():
    matches, delete = match_requests([_claim()], [_request(date=None)], NOW)

    assert matches == []
    assert delete == [777]


def test_unmatched_fresh_request_is_kept_for_a_claim_that_may_come():
    fresh = _request(name="Somepoop", date="2026-11-17 21:04:00", bonk_id=5)

    _, delete = match_requests([_claim()], [fresh], NOW)

    assert delete == []


def test_unmatched_stale_request_is_deleted():
    stale = _request(name="Somepoop", date="2026-11-17 20:40:00", bonk_id=5)

    _, delete = match_requests([_claim()], [stale], NOW)

    assert delete == [5]


def test_each_request_proves_one_claim():
    first = _claim(claim_id=1, started=STARTED)
    second = _claim(claim_id=2, started=STARTED + timedelta(seconds=10), discord_id=43)

    matches, _ = match_requests([second, first], [_request()], NOW)

    assert [m.claim.id for m in matches] == [1]  # the earlier claim


def test_other_names_dont_match():
    matches, _ = match_requests([_claim("Somepoop")], [_request("The Saucy Tulip")], NOW)

    assert matches == []


@pytest.mark.parametrize(
    "name, ok",
    [("The Saucy Tulip", True), (" Spaced", True), ("", False), ("   ", False),
     ("x" * 65, False), ("bad\nname", False), ("Boſsman", False), ("Boßman", False),
     ("ﬁsh", False), ("Café", False), ("snake_case 2", True)],
)
def test_name_problem(name, ok):
    assert (name_problem(name) is None) == ok


def test_name_key():
    assert name_key("The Saucy Tulip") == name_key("the saucy TULIP")
    assert name_key(" The Saucy Tulip") != name_key("The Saucy Tulip")


@pytest.mark.parametrize("lookalike", ["Boſsman", "Boßman", "BOSSMAN\u212a"])
def test_lookalike_letters_dont_fold(lookalike):
    """casefold() turned these into "bossman" while the database's lower() kept
    them distinct, so a claim for a lookalike took the real player's request."""
    assert name_key(lookalike) != name_key("Bossman")


def test_a_lookalike_claim_cant_take_the_real_players_request():
    attacker = _claim("Boſsman", claim_id=1, discord_id=666)
    victim = _claim("Bossman", claim_id=2, started=STARTED + timedelta(seconds=30), discord_id=42)

    matches, _ = match_requests([attacker, victim], [_request("Bossman")], NOW)

    assert [(m.claim.id, m.claim.discord_user_id) for m in matches] == [(2, 42)]


def test_name_key_folds_like_postgres_lower_for_ascii():
    assert name_key("The Saucy Tulip_9") == "the saucy tulip_9"
