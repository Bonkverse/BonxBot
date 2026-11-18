"""#skin-feed (features/site/feed.py). bonkverse.io and the channel are faked;
the state test needs TEST_DATABASE_URL."""

import asyncio

import pytest

from features.site import feed
from services import bonkverse_client as site
from tests.test_accounts_service import URL, _sql, fresh_db  # noqa: F401


def skin(i, source="website"):
    return {"id": i, "name": f"Skin {i}", "creator": "Somepoop", "source": source,
            "url": f"https://bonkverse.io/skins/detail/{i}", "image_url": f"https://x/{i}.png"}


def test_card_says_where_it_came_from():
    embed = feed.feed_card(skin(7, "editor"))
    assert (embed.title, embed.url, embed.image.url) == ("Skin 7", skin(7)["url"], "https://x/7.png")
    assert embed.footer.text == "Skin #7 · Made in the Bonkverse skin editor"
    assert feed.feed_card(skin(8, "legacy")).footer.text == "Skin #8"


def test_buttons_name_the_skin_when_there_are_several():
    async def build():
        one = feed.feed_view([skin(1)])
        many = feed.feed_view([skin(1), skin(2)])
        return [b.item.label for b in one.children], [b.item.custom_id for b in many.children]

    labels, ids = asyncio.run(build())
    assert labels == ["⭐ Collect"]
    assert ids == ["feed:collect:1", "feed:collect:2"]


def test_buttons_rebuild_from_their_id_after_a_restart():
    match = feed.FeedCollect.__discord_ui_compiled_template__.fullmatch("feed:collect:5653")
    button = asyncio.run(feed.FeedCollect.from_custom_id(None, None, match))
    assert button.skin_id == 5653


def test_channel_setting(monkeypatch):
    monkeypatch.setenv("SKIN_FEED_CHANNEL_ID", "123")
    assert feed.channel_id() == 123
    monkeypatch.setenv("SKIN_FEED_CHANNEL_ID", "")
    assert feed.channel_id() is None


class FakeChannel:
    def __init__(self):
        self.posts = []

    async def send(self, **kwargs):
        self.posts.append(kwargs)


@pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL to run database tests")
def test_first_tick_starts_from_now_then_posts_only_new_skins(monkeypatch):
    asked = []
    site_skins = {"newest": 100, "new": [skin(101), skin(102)]}

    async def latest(after=None, limit=10):
        asked.append(after)
        if after is None:
            return site.SiteReply(data={"newest_id": site_skins["newest"], "skins": []})
        return site.SiteReply(data={"newest_id": 102, "skins": [s for s in site_skins["new"] if s["id"] > after]})

    monkeypatch.setattr(site, "latest_skins", latest)
    channel = FakeChannel()

    async def go():
        await feed.ensure_schema()
        first = await feed.tick(channel)  # no backlog posted
        second = await feed.tick(channel)  # 101 and 102
        third = await feed.tick(channel)  # nothing new
        return first, second, third, await feed.load_after()

    first, second, third, after = asyncio.get_event_loop().run_until_complete(go())
    assert (first, second, third, after) == (0, 2, 0, 102)
    assert asked == [None, 100, 102]
    assert [e.title for e in channel.posts[0]["embeds"]] == ["Skin 101", "Skin 102"]


@pytest.mark.skipif(not URL, reason="set TEST_DATABASE_URL to run database tests")
def test_site_down_keeps_the_place(monkeypatch):
    async def down(after=None, limit=10):
        return site.SiteReply(error="unreachable")

    monkeypatch.setattr(site, "latest_skins", down)

    async def go():
        await feed.ensure_schema()
        await feed.save_after(50)
        posted = await feed.tick(FakeChannel())
        return posted, await feed.load_after()

    assert asyncio.get_event_loop().run_until_complete(go()) == (0, 50)
