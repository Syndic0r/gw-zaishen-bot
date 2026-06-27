#!/usr/bin/env python3
"""
Guild Wars 1 Zaishen daily-quest rotation - pure, dependency-free, testable.

The four Zaishen Challenge Quests rotate on fixed cycles and change once a day at **16:00 UTC**
(fixed year-round; GW1 does not observe DST). A "Zaishen day" therefore runs from one 16:00 UTC to
the next, so a timestamp before 16:00 UTC still belongs to the *previous* calendar day's quest set.

Each cycle advances exactly +1 index per Zaishen day. The active index is:

    index = (ANCHOR[type] + days_since_epoch) % len(CYCLE[type])

where days_since_epoch is counted in Zaishen days from EPOCH. EPOCH + ANCHOR were taken from the
official Guild Wars Wiki schedule and validated against 8 consecutive dated days
(2026-06-23 … 2026-06-30) for all four cycles - see tests/test_zaishen.py.

Sources: https://wiki.guildwars.com/wiki/Zaishen_Challenge_Quests and the per-quest /cycles pages.
"""

from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

RESET_HOUR_UTC = 16  # quests change daily at 16:00 UTC (fixed, no DST)

# The Zaishen day beginning 2026-06-23 16:00 UTC, and the 0-based cycle index active on it.
EPOCH = date(2026, 6, 23)
ANCHOR = {"mission": 2, "bounty": 17, "combat": 12, "vanquish": 15}

# Human labels + emoji for each quest type, in display order.
QUEST_TYPES = [
    ("mission", "🗺️", "Zaishen Mission"),
    ("bounty", "🎯", "Zaishen Bounty"),
    ("combat", "⚔️", "Zaishen Combat"),
    ("vanquish", "💀", "Zaishen Vanquish"),
]

# Full ordered rotation lists (verbatim from wiki.guildwars.com). Combat deliberately repeats modes
# within its 28-day cycle - store the full list; never dedupe or use list.index() to find the offset.
CYCLES = {
    "mission": [
        "Augury Rock",
        "Grand Court of Sebelkeh",
        "Ice Caves of Sorrow",
        "Raisu Palace",
        "Gate of Desolation",
        "Thirsty River",
        "Blacktide Den",
        "Against the Charr",
        "Abaddon's Mouth",
        "Nundu Bay",
        "Divinity Coast",
        "Zen Daijun",
        "Pogahn Passage",
        "Tahnnakai Temple",
        "The Great Northern Wall",
        "Dasha Vestibule",
        "The Wilds",
        "Unwaking Waters",
        "Chahbek Village",
        "Aurora Glade",
        "A Time for Heroes",
        "Consulate Docks",
        "Ring of Fire",
        "Nahpui Quarter",
        "The Dragon's Lair",
        "Dzagonur Bastion",
        "D'Alessio Seaboard",
        "Assault on the Stronghold",
        "The Eternal Grove",
        "Sanctum Cay",
        "Rilohn Refuge",
        "Warband of Brothers",
        "Borlis Pass",
        "Imperial Sanctum",
        "Moddok Crevice",
        "Nolani Academy",
        "Destruction's Depths",
        "Venta Cemetery",
        "Fort Ranik",
        "A Gate Too Far",
        "Minister Cho's Estate",
        "Thunderhead Keep",
        "Tihark Orchard",
        "Finding the Bloodstone",
        "Dunes of Despair",
        "Vizunah Square",
        "Jokanur Diggings",
        "Iron Mines of Moladune",
        "Kodonur Crossroads",
        "G.O.L.E.M.",
        "Arborstone",
        "Gates of Kryta",
        "Gate of Madness",
        "The Elusive Golemancer",
        "Riverside Province",
        "Boreas Seabed",
        "Ruins of Morah",
        "Hell's Precipice",
        "Ruins of Surmia",
        "Curse of the Nornbear",
    ],
    "bounty": [
        "Droajam, Mage of the Sands",
        "Royen Beastkeeper",
        "Eldritch Ettin",
        "Vengeful Aatxe",
        "Fronis Irontoe",
        "Urgoz",
        "Fenrir",
        "Selvetarm",
        "Mohby Windbeak",
        "Charged Blackness",
        "Rotscale",
        "Zoldark the Unholy",
        "Korshek the Immolated",
        "Myish, Lady of the Lake",
        "Frostmaw the Kinslayer",
        "Kunvie Firewing",
        "Z'him Monns",
        "The Greater Darkness",
        "TPS Regulator Golem",
        "Plague of Destruction",
        "The Darknesses",
        "Admiral Kantoh",
        "Borrguus Blisterbark",
        "Forgewight",
        "Baubao Wavewrath",
        "Joffs the Mitigator",
        "Rragar Maneater",
        "Chung, the Attuned",
        "Lord Jadoth",
        "Nulfastu, Earthbound",
        "The Iron Forgeman",
        "Magmus",
        "Mobrin, Lord of the Marsh",
        "Jarimiya the Unmerciful",
        "Duncan the Black",
        "Quansong Spiritspeak",
        "The Stygian Underlords",
        "Fozzy Yeoryios",
        "The Black Beast of Arrgh",
        "Arachni",
        "The Four Horsemen",
        "Remnant of Antiquities",
        "Arbor Earthcall",
        "Prismatic Ooze",
        "Lord Khobay",
        "Jedeh the Mighty",
        "Ssuns, Blessed of Dwayna",
        "Justiciar Thommis",
        "Harn and Maxine Coldstone",
        "Pywatt the Swift",
    ],
    "combat": [
        "Jade Quarry",
        "Codex Arena",
        "Heroes' Ascent",
        "Guild Versus Guild",
        "Alliance Battles",
        "Heroes' Ascent",
        "Guild Versus Guild",
        "Codex Arena",
        "Fort Aspenwood",
        "Jade Quarry",
        "Random Arena",
        "Codex Arena",
        "Guild Versus Guild",
        "Jade Quarry",
        "Alliance Battles",
        "Heroes' Ascent",
        "Random Arena",
        "Fort Aspenwood",
        "Jade Quarry",
        "Random Arena",
        "Fort Aspenwood",
        "Heroes' Ascent",
        "Alliance Battles",
        "Guild Versus Guild",
        "Codex Arena",
        "Random Arena",
        "Fort Aspenwood",
        "Alliance Battles",
    ],
    "vanquish": [
        "Jaya Bluffs",
        "Holdings of Chokhin",
        "Ice Cliff Chasms",
        "Griffon's Mouth",
        "Kinya Province",
        "Issnur Isles",
        "Jaga Moraine",
        "Ice Floe",
        "Maishang Hills",
        "Jahai Bluffs",
        "Riven Earth",
        "Icedome",
        "Minister Cho's Estate",
        "Mehtani Keys",
        "Sacnoth Valley",
        "Iron Horse Mine",
        "Morostav Trail",
        "Plains of Jarin",
        "Sparkfly Swamp",
        "Kessex Peak",
        "Mourning Veil Falls",
        "The Alkali Pan",
        "Varajar Fells",
        "Lornar's Pass",
        "Pongmei Valley",
        "The Floodplain of Mahnkelon",
        "Verdant Cascades",
        "Majesty's Rest",
        "Raisu Palace",
        "The Hidden City of Ahdashim",
        "Rhea's Crater",
        "Mamnoon Lagoon",
        "Shadow's Passage",
        "The Mirror of Lyss",
        "Saoshang Trail",
        "Nebo Terrace",
        "Shenzun Tunnels",
        "The Ruptured Heart",
        "Salt Flats",
        "North Kryta Province",
        "Silent Surf",
        "The Shattered Ravines",
        "Scoundrel's Rise",
        "Old Ascalon",
        "Sunjiang District",
        "The Sulfurous Wastes",
        "Magus Stones",
        "Perdition Rock",
        "Sunqua Vale",
        "Turai's Procession",
        "Norrhart Domains",
        "Pockmark Flats",
        "Tahnnakai Temple",
        "Vehjin Mines",
        "Poisoned Outcrops",
        "Prophet's Path",
        "The Eternal Grove",
        "Tasca's Demise",
        "Resplendent Makuun",
        "Reed Bog",
        "Unwaking Waters",
        "Stingray Strand",
        "Sunward Marches",
        "Regent Valley",
        "Wajjun Bazaar",
        "Yatendi Canyons",
        "Twin Serpent Lakes",
        "Sage Lands",
        "Xaquang Skyway",
        "Zehlon Reach",
        "Tangle Root",
        "Silverwood",
        "Zen Daijun",
        "The Arid Sea",
        "Nahpui Quarter",
        "Skyward Reach",
        "The Scar",
        "The Black Curtain",
        "Panjiang Peninsula",
        "Snake Dance",
        "Traveler's Vale",
        "The Breach",
        "Lahtenda Bog",
        "Spearhead Peak",
        "Mount Qinkai",
        "Marga Coast",
        "Melandru's Hope",
        "The Falls",
        "Joko's Domain",
        "Vulture Drifts",
        "Wilderness of Bahdza",
        "Talmark Wilderness",
        "Vehtendi Valley",
        "Talus Chute",
        "Mineral Springs",
        "Anvil Rock",
        "Arborstone",
        "Witman's Folly",
        "Arkjok Ward",
        "Ascalon Foothills",
        "Bahdok Caverns",
        "Cursed Lands",
        "Alcazia Tangle",
        "Archipelagos",
        "Eastern Frontier",
        "Dejarin Estate",
        "Watchtower Coast",
        "Arbor Bay",
        "Barbarous Shore",
        "Deldrimor Bowl",
        "Boreas Seabed",
        "Cliffs of Dohjok",
        "Diessa Lowlands",
        "Bukdek Byway",
        "Bjora Marches",
        "Crystal Overlook",
        "Diviner's Ascent",
        "Dalada Uplands",
        "Drazach Thicket",
        "Fahranur, the First City",
        "Dragon's Gullet",
        "Ferndale",
        "Forum Highlands",
        "Dreadnought's Drift",
        "Drakkar Lake",
        "Dry Top",
        "Tears of the Fallen",
        "Gyala Hatchery",
        "Ettin's Back",
        "Gandara, the Moon Fortress",
        "Grothmar Wardowns",
        "Flame Temple Corridor",
        "Haiju Lagoon",
        "Frozen Forest",
        "Garden of Seborhin",
        "Grenth's Footprint",
    ],
}


def zaishen_day(now=None):
    """Return the date() of the Zaishen day that `now` (aware UTC datetime) falls in.
    The day flips at 16:00 UTC, so we shift back 16h before taking the date."""
    if now is None:
        now = datetime.now(timezone.utc)
    return (now.astimezone(timezone.utc) - timedelta(hours=RESET_HOUR_UTC)).date()


def next_reset(now=None):
    """Return the aware-UTC datetime of the next 16:00 UTC reset at or after `now`."""
    if now is None:
        now = datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    today_reset = datetime(now.year, now.month, now.day, RESET_HOUR_UTC, tzinfo=timezone.utc)
    return today_reset if now < today_reset else today_reset + timedelta(days=1)


def index_for(qtype, day):
    """0-based active index for `qtype` on Zaishen day `day` (a date())."""
    days = (day - EPOCH).days
    return (ANCHOR[qtype] + days) % len(CYCLES[qtype])


def quest_for(qtype, day=None):
    """Active quest name for `qtype` on the Zaishen day containing `day`/now."""
    if day is None:
        day = zaishen_day()
    return CYCLES[qtype][index_for(qtype, day)]


def all_quests(day=None):
    """Return [(qtype, emoji, label, quest_name), ...] for the given Zaishen day (default: now)."""
    if day is None:
        day = zaishen_day()
    return [(qt, emoji, label, CYCLES[qt][index_for(qt, day)]) for qt, emoji, label in QUEST_TYPES]


def wiki_url(name):
    """Build the Guild Wars Wiki article URL for a quest/area/boss name."""
    return "https://wiki.guildwars.com/wiki/" + quote(name.replace(" ", "_"))
