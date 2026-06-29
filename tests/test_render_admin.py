"""Tests for the message renderer (render.content) and the bot-admin permission check
(_is_bot_admin) - the two bits of presentation/permission logic that the storage suite doesn't cover.

render.content is checked with golden strings (the DM `guild_id=None` path and the in-server roster
path); _is_bot_admin is checked against a tiny fake interaction object so we don't need a live
Discord connection."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

import render
import storage
import zaishen

ZDAY = date(2026, 6, 27)  # mission = Blacktide Den (verified in test_zaishen)
# The 16:00 UTC reset that STARTS that Zaishen day, and the next reset - both pinned so the golden
# strings are deterministic regardless of the real clock.
DAY_EPOCH = int(datetime(2026, 6, 27, 16, tzinfo=timezone.utc).timestamp())
NEXT_RESET = datetime(2026, 6, 28, 16, tzinfo=timezone.utc)
HEADER = f"# ⚔️ Guild Wars - Zaishen Dailies\n📅 **<t:{DAY_EPOCH}:D>** · next reset <t:{int(NEXT_RESET.timestamp())}:R>\n"


@pytest.fixture(autouse=True)
def _freeze(monkeypatch):
    monkeypatch.setattr(zaishen, "zaishen_day", lambda now=None: ZDAY)
    monkeypatch.setattr(zaishen, "next_reset", lambda now=None: NEXT_RESET)


def _fresh(tmp_path):
    storage.init(str(tmp_path / "t.db"), migrate=False)


# ---- render.content --------------------------------------------------------
def test_content_dm_path_no_roster(tmp_path):
    _fresh(tmp_path)
    out = render.content(None)  # DM path: no guild -> quests only, no roster lines, no footer
    assert out == (
        HEADER + "\n"
        "🗺️ **Zaishen Mission:** [Blacktide Den](https://wiki.guildwars.com/wiki/Blacktide_Den)\n"
        "🎯 **Zaishen Bounty:** [Admiral Kantoh](https://wiki.guildwars.com/wiki/Admiral_Kantoh)\n"
        "⚔️ **Zaishen Combat:** [Random Arena](https://wiki.guildwars.com/wiki/Random_Arena)\n"
        "💀 **Zaishen Vanquish:** [Kessex Peak](https://wiki.guildwars.com/wiki/Kessex_Peak)"
    )
    assert "nobody yet" not in out and "Tap a quest" not in out


def test_content_guild_empty_roster(tmp_path):
    _fresh(tmp_path)
    out = render.content(111)  # in-server, nobody signed up yet
    assert out == (
        HEADER + "\n"
        "🗺️ **Zaishen Mission:** [Blacktide Den](https://wiki.guildwars.com/wiki/Blacktide_Den)\n"
        "   └ _nobody yet_\n"
        "🎯 **Zaishen Bounty:** [Admiral Kantoh](https://wiki.guildwars.com/wiki/Admiral_Kantoh)\n"
        "   └ _nobody yet_\n"
        "⚔️ **Zaishen Combat:** [Random Arena](https://wiki.guildwars.com/wiki/Random_Arena)\n"
        "   └ _nobody yet_\n"
        "💀 **Zaishen Vanquish:** [Kessex Peak](https://wiki.guildwars.com/wiki/Kessex_Peak)\n"
        "   └ _nobody yet_\n"
        "\n-# Tap a quest to sign up • ✅ All for everything • 🧹 to sign off all."
    )


def test_content_guild_with_signups_and_favorite(tmp_path):
    _fresh(tmp_path)
    storage.toggle(111, ZDAY.isoformat(), "mission", 7)  # plain handle
    storage.toggle(111, ZDAY.isoformat(), "mission", 8)  # has a favorite IGN + profession
    storage.add_ign(8, "Dervish McStab", "Dervish")
    storage.set_favorite(8, "Dervish McStab")
    out = render.content(111)
    # roster line shows the two users in sign-up order; the one with a favorite shows name+profession
    assert "🗺️ **Zaishen Mission:** [Blacktide Den]" in out
    assert "   └ up (2): <@7>, <@8> (Dervish McStab, Dervish)" in out


def test_content_escapes_markdown_in_ign(tmp_path):
    _fresh(tmp_path)
    storage.toggle(111, ZDAY.isoformat(), "combat", 9)
    storage.add_ign(9, "Bold*Name")  # markdown-y IGN must be escaped so it can't distort the roster
    storage.set_favorite(9, "Bold*Name")
    out = render.content(111)
    assert "<@9> (Bold\\*Name)" in out  # the asterisk is backslash-escaped


# ---- _is_bot_admin ---------------------------------------------------------
def _interaction(*, manage_guild, guild_id=111, role_ids=()):
    """A tiny stand-in for discord.Interaction with just the attributes _is_bot_admin reads."""
    roles = [SimpleNamespace(id=r) for r in role_ids]
    user = SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_guild=manage_guild), roles=roles
    )
    return SimpleNamespace(user=user, guild_id=guild_id)


def test_is_bot_admin_manage_guild(tmp_path):
    _fresh(tmp_path)
    import bot

    assert bot._is_bot_admin(_interaction(manage_guild=True)) is True


def test_is_bot_admin_via_configured_role(tmp_path):
    _fresh(tmp_path)
    import bot

    storage.set_admin_role(111, 4242)
    # has the configured admin role -> allowed even without Manage Server
    assert bot._is_bot_admin(_interaction(manage_guild=False, role_ids=(4242,))) is True
    # a different role -> denied
    assert bot._is_bot_admin(_interaction(manage_guild=False, role_ids=(99,))) is False


def test_is_bot_admin_no_role_no_perm(tmp_path):
    _fresh(tmp_path)
    import bot

    # no admin role configured and no Manage Server -> denied
    assert bot._is_bot_admin(_interaction(manage_guild=False, role_ids=())) is False
