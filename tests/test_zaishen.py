"""Validate the rotation math against the wiki's published schedule (2026-06-23 … 2026-06-30)."""

from datetime import date, datetime, timezone

import zaishen

# Verbatim from https://wiki.guildwars.com/wiki/Zaishen_Challenge_Quests
# date -> (mission, bounty, combat, vanquish)
SCHEDULE = {
    date(2026, 6, 23): (
        "Ice Caves of Sorrow",
        "The Greater Darkness",
        "Guild Versus Guild",
        "Iron Horse Mine",
    ),
    date(2026, 6, 24): ("Raisu Palace", "TPS Regulator Golem", "Jade Quarry", "Morostav Trail"),
    date(2026, 6, 25): (
        "Gate of Desolation",
        "Plague of Destruction",
        "Alliance Battles",
        "Plains of Jarin",
    ),
    date(2026, 6, 26): ("Thirsty River", "The Darknesses", "Heroes' Ascent", "Sparkfly Swamp"),
    date(2026, 6, 27): ("Blacktide Den", "Admiral Kantoh", "Random Arena", "Kessex Peak"),
    date(2026, 6, 28): (
        "Against the Charr",
        "Borrguus Blisterbark",
        "Fort Aspenwood",
        "Mourning Veil Falls",
    ),
    date(2026, 6, 29): ("Abaddon's Mouth", "Forgewight", "Jade Quarry", "The Alkali Pan"),
    date(2026, 6, 30): ("Nundu Bay", "Baubao Wavewrath", "Random Arena", "Varajar Fells"),
}


def test_full_schedule_matches_wiki():
    for day, (mission, bounty, combat, vanquish) in SCHEDULE.items():
        assert zaishen.quest_for("mission", day) == mission, day
        assert zaishen.quest_for("bounty", day) == bounty, day
        assert zaishen.quest_for("combat", day) == combat, day
        assert zaishen.quest_for("vanquish", day) == vanquish, day


def test_cycle_lengths():
    assert len(zaishen.CYCLES["mission"]) == 60
    assert len(zaishen.CYCLES["bounty"]) == 50
    assert len(zaishen.CYCLES["combat"]) == 28
    assert len(zaishen.CYCLES["vanquish"]) == 136


def test_day_flips_at_1600_utc():
    # 15:59 UTC still belongs to the previous calendar day; 16:00 UTC starts the new one.
    before = datetime(2026, 6, 27, 15, 59, tzinfo=timezone.utc)
    after = datetime(2026, 6, 27, 16, 0, tzinfo=timezone.utc)
    assert zaishen.zaishen_day(before) == date(2026, 6, 26)
    assert zaishen.zaishen_day(after) == date(2026, 6, 27)


def test_next_reset():
    before = datetime(2026, 6, 27, 15, 0, tzinfo=timezone.utc)
    after = datetime(2026, 6, 27, 16, 30, tzinfo=timezone.utc)
    assert zaishen.next_reset(before) == datetime(2026, 6, 27, 16, 0, tzinfo=timezone.utc)
    assert zaishen.next_reset(after) == datetime(2026, 6, 28, 16, 0, tzinfo=timezone.utc)


def test_wraparound():
    # one full mission cycle (60 days) later == same quest
    base = date(2026, 6, 23)
    from datetime import timedelta

    assert zaishen.quest_for("mission", base) == zaishen.quest_for(
        "mission", base + timedelta(days=60)
    )
    assert zaishen.quest_for("combat", base) == zaishen.quest_for(
        "combat", base + timedelta(days=28)
    )


def test_next_occurrence_today_and_future():
    from datetime import timedelta

    today = date(2026, 6, 27)  # mission = Blacktide Den (verified above)
    # the active quest's next occurrence is today itself
    assert zaishen.next_occurrence("mission", "Blacktide Den", today) == today
    # case-insensitive match
    assert zaishen.next_occurrence("mission", "blacktide den", today) == today
    # tomorrow's mission (Against the Charr) is found one day out
    assert zaishen.next_occurrence("mission", "Against the Charr", today) == today + timedelta(
        days=1
    )
    # a name not in the cycle returns None
    assert zaishen.next_occurrence("mission", "Not A Quest", today) is None
    # the returned day always reproduces the quest
    d = zaishen.next_occurrence("vanquish", "Sparkfly Swamp", today)
    assert zaishen.quest_for("vanquish", d) == "Sparkfly Swamp"


def test_types_with_quest():
    # Raisu Palace appears in BOTH the mission and vanquish cycles
    types = [qt for qt, _e, _l, _n in zaishen.types_with_quest("Raisu Palace")]
    assert types == ["mission", "vanquish"]
    # canonical capitalization is returned regardless of input case
    assert zaishen.types_with_quest("thirsty river")[0][3] == "Thirsty River"
    assert zaishen.types_with_quest("nope") == []


def test_all_quest_names_unique_sorted():
    names = zaishen.all_quest_names()
    assert names == sorted(set(names), key=str.casefold)
    assert "Thirsty River" in names and "Sparkfly Swamp" in names
