"""Who is credited as a submitted skin's creator (features/skins/creator.py).

The same cases as Bonkverse's resolve_creator tests, so both sides agree.
"""

import pytest

from features.skins.creator import author_is_mine, creator_for

MINE = ["The Saucy Tulip", "Tulip Alt"]


@pytest.mark.parametrize(
    "author, made_it, expected",
    [
        (None, None, ("The Saucy Tulip", "submitter")),
        ("   ", None, ("The Saucy Tulip", "submitter")),
        ("the saucy TULIP", None, ("The Saucy Tulip", "submitter")),
        ("tulip alt", None, ("Tulip Alt", "submitter")),  # an alt of mine: credit it, no question
        ("Somepoop", None, None),  # ask
        ("Somepoop", True, ("The Saucy Tulip", "submitter")),
        ("Somepoop", False, ("Somepoop", "bonkleagues")),
        (" The Saucy Tulip", None, None),  # spaces count: another account
    ],
)
def test_creator_for(author, made_it, expected):
    assert creator_for(MINE, "The Saucy Tulip", author, made_it) == expected


def test_i_made_this_credits_the_chosen_account():
    assert creator_for(MINE, "Tulip Alt", "Somepoop", True) == ("Tulip Alt", "submitter")


@pytest.mark.parametrize(
    "author, mine", [(None, True), ("", True), ("TULIP ALT", True), ("Somepoop", False)]
)
def test_author_is_mine(author, mine):
    assert author_is_mine(MINE, author) is mine
