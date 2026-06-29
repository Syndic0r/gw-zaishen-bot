"""Builds the daily message text: the four quests, each with its own sign-up roster."""

from datetime import datetime, timezone

import discord

import storage
import zaishen


def _day_start_epoch(day):
    """Unix timestamp of the 16:00 UTC reset that STARTS Zaishen day `day` (a date) - i.e. when that
    day's quests went live. Derived from `day` itself rather than `next_reset() - 86400` arithmetic, so
    the displayed date always matches the day whose quests we render."""
    dt = datetime(day.year, day.month, day.day, zaishen.RESET_HOUR_UTC, tzinfo=timezone.utc)
    return int(dt.timestamp())


def content(guild_id=None):
    """The daily message body - header + each quest (wiki-linked) with who's up for it.

    With a `guild_id`, the per-server roster + the sign-up footer are shown (the pinned message, and
    `/zaishen` used in a server). Without one (e.g. `/zaishen` in a DM), only the quests are shown."""
    day = zaishen.zaishen_day()
    zday = day.isoformat()
    day_epoch = _day_start_epoch(day)  # 16:00 UTC reset that started today's Zaishen day
    reset_epoch = int(zaishen.next_reset().timestamp())  # the upcoming reset
    signs = storage.signups(guild_id, zday) if guild_id is not None else {}
    igns = storage.favorites_for({u for ups in signs.values() for u in ups})

    def who(uid):
        fav = igns.get(uid)  # (favorite character name, profession_or_None), or None -> handle only
        if not fav:
            return f"<@{uid}>"
        name, prof = fav
        # escape the user-supplied IGN so markdown in it can't distort the roster; the message is
        # sent with allowed_mentions=none, so any mention-like text in it never pings either.
        label = discord.utils.escape_markdown(name)
        if prof:
            label += f", {prof}"
        return f"<@{uid}> ({label})"

    lines = [
        "# ⚔️ Guild Wars - Zaishen Dailies",
        f"📅 **<t:{day_epoch}:D>** · next reset <t:{reset_epoch}:R>",
        "",
    ]
    for qt, emoji, label, name in zaishen.all_quests(day):
        lines.append(f"{emoji} **{label}:** [{name}]({zaishen.wiki_url(name)})")
        if guild_id is None:
            continue
        ups = signs.get(qt, [])
        if ups:
            lines.append(f"   └ up ({len(ups)}): " + ", ".join(who(u) for u in ups))
        else:
            lines.append("   └ _nobody yet_")

    if guild_id is not None:
        lines.append("")
        lines.append("-# Tap a quest to sign up • ✅ All for everything • 🧹 to sign off all.")
    return "\n".join(lines)
