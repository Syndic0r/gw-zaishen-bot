"""Storage layer tests — per-guild signups, config, history, migration, against a temp SQLite DB."""

import sqlite3
from datetime import date

import config
import storage
import zaishen

QTS = [qt for qt, _e, _l in zaishen.QUEST_TYPES]  # mission, bounty, combat, vanquish
ZDAY = "2026-06-27"
G1 = 111  # a guild id
G2 = 222  # another guild id


def fresh(tmp_path):
    storage.init(str(tmp_path / "t.db"), migrate=False)


# ---- per-mission signups (now scoped to a guild) ---------------------------
def test_toggle_per_quest(tmp_path):
    fresh(tmp_path)
    assert storage.signups(G1, ZDAY)["mission"] == []
    assert storage.toggle(G1, ZDAY, "mission", 1) is True  # sign up
    assert storage.signups(G1, ZDAY)["mission"] == [1]
    assert storage.signups(G1, ZDAY)["bounty"] == []  # independent per quest
    assert storage.toggle(G1, ZDAY, "mission", 1) is False  # toggle off
    assert storage.signups(G1, ZDAY)["mission"] == []


def test_multiple_users_and_order(tmp_path):
    fresh(tmp_path)
    storage.toggle(G1, ZDAY, "combat", 10)
    storage.toggle(G1, ZDAY, "combat", 20)
    storage.toggle(G1, ZDAY, "combat", 30)
    assert storage.signups(G1, ZDAY)["combat"] == [10, 20, 30]  # sign-up order preserved
    storage.toggle(G1, ZDAY, "combat", 20)  # 20 signs off
    assert storage.signups(G1, ZDAY)["combat"] == [10, 30]


def test_sign_all_and_off_all(tmp_path):
    fresh(tmp_path)
    storage.sign_all(G1, ZDAY, 7)
    for qt in QTS:
        assert 7 in storage.signups(G1, ZDAY)[qt]
    storage.sign_all(G1, ZDAY, 7)  # idempotent — no duplicates / errors
    assert storage.signups(G1, ZDAY)["mission"] == [7]
    storage.toggle(G1, ZDAY, "vanquish", 8)  # someone else on one quest
    storage.sign_off_all(G1, ZDAY, 7)
    for qt in QTS:
        assert 7 not in storage.signups(G1, ZDAY)[qt]
    assert storage.signups(G1, ZDAY)["vanquish"] == [8]  # only 7 was cleared


def test_signups_isolated_per_day(tmp_path):
    fresh(tmp_path)
    storage.toggle(G1, ZDAY, "mission", 1)
    assert storage.signups(G1, "2026-06-28")["mission"] == []  # a different day is empty


def test_signups_isolated_per_guild(tmp_path):
    fresh(tmp_path)
    storage.toggle(G1, ZDAY, "mission", 7)
    storage.sign_all(G1, ZDAY, 8)
    assert storage.signups(G1, ZDAY)["mission"] == [7, 8]
    assert storage.signups(G2, ZDAY)["mission"] == []  # another guild is unaffected
    assert storage.signups(G2, ZDAY)["combat"] == []


# ---- per-guild configuration -----------------------------------------------
def test_guild_config_and_configured(tmp_path):
    fresh(tmp_path)
    assert storage.get_guild_config(G1) is None
    assert storage.configured_guilds() == []
    storage.set_guild_config(G1, 100, 200)
    gc = storage.get_guild_config(G1)
    assert (gc["channel_id"], gc["ping_role_id"], gc["enabled"]) == (100, 200, 1)
    storage.set_guild_config(G1, 101, None)  # update channel, drop ping role
    gc = storage.get_guild_config(G1)
    assert gc["channel_id"] == 101 and gc["ping_role_id"] is None
    assert [r["guild_id"] for r in storage.configured_guilds()] == [G1]


def test_disable_enable_and_delete_guild(tmp_path):
    fresh(tmp_path)
    storage.set_guild_config(G1, 100, None)
    storage.toggle(G1, ZDAY, "mission", 7)
    assert storage.disable_guild(G1) is True
    assert storage.configured_guilds() == []  # disabled → not active
    assert storage.get_guild_config(G1)["enabled"] == 0  # but config + signups are kept
    assert storage.signups(G1, ZDAY)["mission"] == [7]
    assert storage.enable_guild(G1) == "enabled"  # resume with the saved channel
    assert [r["guild_id"] for r in storage.configured_guilds()] == [G1]
    storage.delete_guild(G1)  # left the server
    assert storage.get_guild_config(G1) is None
    assert storage.signups(G1, ZDAY)["mission"] == []  # purged


def test_admin_role(tmp_path):
    fresh(tmp_path)
    # works even before /setup — upserts a config row
    storage.set_admin_role(G1, 4242)
    assert storage.get_guild_config(G1)["admin_role_id"] == 4242
    storage.set_guild_config(G1, 100, None)  # later /setup keeps the admin role
    assert storage.get_guild_config(G1)["admin_role_id"] == 4242
    assert storage.get_guild_config(G1)["channel_id"] == 100
    storage.set_admin_role(G1, None)  # clear
    assert storage.get_guild_config(G1)["admin_role_id"] is None


def test_enable_guild_states(tmp_path):
    fresh(tmp_path)
    assert storage.enable_guild(G1) == "absent"  # never set up
    storage.set_guild_config(G1, 100, None)
    assert storage.enable_guild(G1) == "already"  # set_guild_config enables it
    storage.disable_guild(G1)
    assert storage.enable_guild(G1) == "enabled"  # flipped back on


# ---- pinned message rollover -----------------------------------------------
def test_rollover_first_post_and_new_day(tmp_path):
    fresh(tmp_path)
    new_day, mid, first_ever = storage.begin_day_rollover(G1)
    assert (new_day, mid, first_ever) == (True, None, True)  # never posted → first ever
    storage.set_message_id(G1, 999)
    assert storage.message_id(G1) == 999
    new_day, mid, first_ever = storage.begin_day_rollover(G1)
    assert (new_day, first_ever) == (False, False)  # same day, already posted
    assert mid == 999


# ---- history ---------------------------------------------------------------
def test_signup_history(tmp_path):
    fresh(tmp_path)
    storage.ensure_daily("2026-06-26")
    storage.ensure_daily("2026-06-27")
    storage.toggle(G1, "2026-06-26", "mission", 7)
    storage.toggle(G1, "2026-06-27", "combat", 8)
    storage.toggle(G2, "2026-06-27", "combat", 9)  # other guild — excluded
    hist = storage.signup_history(G1, 7)
    assert [z for z, _ in hist] == ["2026-06-27", "2026-06-26"]  # newest first
    d27 = {qt: ups for qt, _name, ups in hist[0][1]}
    assert d27 == {"combat": [8]}  # only quests that had sign-ups
    name = next(n for qt, n, _ in hist[1][1] if qt == "mission")
    assert name == zaishen.quest_for("mission", date(2026, 6, 26))  # quest name joined from daily
    assert len(storage.signup_history(G1, 1)) == 1  # day limit honored


def test_history_retention_states_and_clear(tmp_path):
    fresh(tmp_path)
    assert storage.set_history_retention(G1, False) == "absent"  # no config yet
    storage.set_guild_config(G1, 100, None)
    assert storage.get_guild_config(G1)["keep_history"] == 1  # kept by default
    assert storage.set_history_retention(G1, True) == "already"
    assert storage.set_history_retention(G1, False) == "changed"
    assert storage.get_guild_config(G1)["keep_history"] == 0
    # clear removes past days but leaves today's live roster
    storage.toggle(G1, "2026-06-20", "mission", 7)  # a past day
    storage.toggle(G1, ZDAY, "mission", 7)  # today
    assert storage.clear_history(G1) == 1  # one past day cleared
    assert [z for z, _ in storage.signup_history(G1, 7)] == [ZDAY]  # only today remains
    assert storage.signups(G1, ZDAY)["mission"] == [7]  # today untouched


def test_disabled_history_purged_at_rollover(tmp_path):
    fresh(tmp_path)
    storage.set_guild_config(G1, 100, None)
    storage.set_history_retention(G1, False)
    storage.toggle(G1, "2026-06-20", "mission", 7)  # an old day's sign-up
    storage.set_message_id(G1, 1)  # pin a message dated "today" so the next call sees a NEW day
    # force the stored pinned day to be old, then roll over -> past signups should be purged
    storage._db().execute("UPDATE pinned SET zday = '2000-01-01' WHERE guild_id = ?", (G1,))
    storage._db().commit()
    new_day, _mid, _first = storage.begin_day_rollover(G1)
    assert new_day is True
    assert storage.signup_history(G1, 30) == []  # the old day was dropped on rollover


# ---- migration from the single-tenant schema -------------------------------
def test_migration_from_single_tenant(tmp_path, monkeypatch):
    db = str(tmp_path / "legacy.db")
    c = sqlite3.connect(db)
    c.executescript(
        """
        CREATE TABLE pinned (id INTEGER PRIMARY KEY CHECK (id=1), message_id INTEGER, zday TEXT);
        CREATE TABLE daily (zday TEXT, quest_type TEXT, quest_name TEXT, PRIMARY KEY(zday,quest_type));
        CREATE TABLE signup (zday TEXT, quest_type TEXT, user_id INTEGER, signed_up_at TEXT,
                             PRIMARY KEY(zday,quest_type,user_id));
        CREATE TABLE ign (user_id INTEGER PRIMARY KEY, name TEXT, set_at TEXT);
        """
    )
    c.execute("INSERT INTO pinned VALUES (1, 555, '2026-06-26')")
    c.execute("INSERT INTO signup VALUES ('2026-06-26','mission',42,'2026-06-26T10:00:00+00:00')")
    c.execute("INSERT INTO ign VALUES (42, 'Dervish McStab', '2026-06-26T10:00:00+00:00')")
    c.commit()
    c.close()

    monkeypatch.setattr(config, "GUILD_ID", "1098")  # home guild the legacy rows belong to
    monkeypatch.setattr(config, "CHANNEL_ID", 1361)
    monkeypatch.setattr(config, "PING_ROLE_ID", 0)
    storage.init(db, migrate=False)

    gid = 1098
    assert storage.message_id(gid) == 555  # pinned message adopted
    gc = storage.get_guild_config(gid)
    assert gc["channel_id"] == 1361 and gc["enabled"] == 1  # config seeded from env
    assert storage.signups(gid, "2026-06-26")["mission"] == [42]  # signups carried over w/ guild_id
    assert storage.get_ign(42) == "Dervish McStab"  # global ign table preserved
    assert not storage._has_table("_legacy_pinned")  # stash cleaned up
    assert not storage._has_table("_legacy_signup")


# ---- IGN (global per user) -------------------------------------------------
def test_ign_set_get_clear(tmp_path):
    fresh(tmp_path)
    assert storage.get_ign(1) is None
    storage.set_ign(1, "Dervish McStab")
    assert storage.get_ign(1) == "Dervish McStab"
    storage.set_ign(1, "Mesmer Mary")  # update in place
    assert storage.get_ign(1) == "Mesmer Mary"
    assert storage.clear_ign(1) is True
    assert storage.get_ign(1) is None
    assert storage.clear_ign(1) is False  # already gone


def test_ign_clean_input():
    from commands import _clean_ign

    assert _clean_ign("  Dervish   McStab  ") == "Dervish McStab"  # whitespace collapsed
    assert _clean_ign("Bob\n\tEvil") == "Bob Evil"  # newlines/tabs -> single space
    assert _clean_ign("Zero​Width") == "ZeroWidth"  # zero-width/control chars dropped


def test_ign_list_and_batch(tmp_path):
    fresh(tmp_path)
    storage.set_ign(2, "bob")
    storage.set_ign(1, "Alice")
    storage.set_ign(3, "charlie")
    assert storage.all_igns() == [
        (1, "Alice"),
        (2, "bob"),
        (3, "charlie"),
    ]  # name, case-insensitive
    assert storage.igns_for([1, 3, 99]) == {1: "Alice", 3: "charlie"}  # 99 unlinked -> absent
    assert storage.igns_for([]) == {}


def test_daily_recorded_for_history(tmp_path):
    fresh(tmp_path)
    storage.ensure_daily(ZDAY)
    rec = storage.dailies(ZDAY)
    assert rec == {qt: zaishen.quest_for(qt, date(2026, 6, 27)) for qt in QTS}
    assert rec["mission"] == "Blacktide Den"  # matches the verified schedule
