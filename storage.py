"""
Persistence layer - the single source of truth for the bot's runtime state, backed by SQLite.

Multi-tenant: the bot serves many Discord servers (guilds) at once, so the per-server state is
keyed by `guild_id`. The day's quests themselves are the same worldwide, so `daily` stays global,
and IGNs are a user attribute, so `ign` stays global too.

Tables:
  guild_config(guild_id, channel_id, ping_role_id, enabled, created_at)  per-server setup (/setup)
  pinned(guild_id, message_id, zday)         the current pinned message, per server
  daily(zday, quest_type, quest_name)        each day's computed quests, recorded for history (global)
  signup(guild_id, zday, quest_type, user_id, signed_up_at)  who's up for which quest, per server
  ign(user_id, name, favorite, set_at)       self-declared GW1 character names (global, per user;
                                             a user can have several, with at most one favorite)

`zaishen.py` stays the source of truth for *computing* dailies; `daily` is a recorded copy so the
schedule + signups can be queried/joined and kept as history. Signups are NOT deleted at the daily
rollover - the message just stops showing older days - so the full sign-up history is queryable.
All writes commit immediately; callers hold `lock` around any read-modify-render sequence that must
stay consistent with the keep-at-bottom loop. SQLite calls are synchronous but tiny (local file).
"""

import asyncio
import json
import sqlite3
from datetime import date, datetime, timezone

import config
import zaishen

lock = asyncio.Lock()  # serializes state mutations + the post/edit dance
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id      INTEGER PRIMARY KEY,
    channel_id    INTEGER,
    ping_role_id  INTEGER,
    admin_role_id INTEGER,  -- a role allowed to run admin commands (besides Manage Server)
    enabled       INTEGER NOT NULL DEFAULT 1,
    keep_history  INTEGER NOT NULL DEFAULT 1,  -- retain past days' signups for /history show
    created_at    TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS pinned (
    guild_id   INTEGER PRIMARY KEY,
    message_id INTEGER,
    zday       TEXT
);
CREATE TABLE IF NOT EXISTS daily (
    zday       TEXT NOT NULL,
    quest_type TEXT NOT NULL,
    quest_name TEXT NOT NULL,
    PRIMARY KEY (zday, quest_type)
);
CREATE TABLE IF NOT EXISTS signup (
    guild_id     INTEGER NOT NULL,
    zday         TEXT    NOT NULL,
    quest_type   TEXT    NOT NULL,
    user_id      INTEGER NOT NULL,
    signed_up_at TEXT    NOT NULL,
    PRIMARY KEY (guild_id, zday, quest_type, user_id)
);
CREATE TABLE IF NOT EXISTS ign (
    user_id  INTEGER NOT NULL,
    name     TEXT    NOT NULL,
    favorite INTEGER NOT NULL DEFAULT 0,  -- the one shown on the roster (at most one set per user)
    set_at   TEXT    NOT NULL,
    PRIMARY KEY (user_id, name)
);
"""

MAX_IGNS = 12  # GW1 accounts have a handful of character slots; cap to keep lists sane


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _db():
    return _conn


def init(db_path=None, migrate=True):
    """Open the DB (creating tables) - call once on startup. Tests pass a temp path + migrate=False."""
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = sqlite3.connect(db_path or config.DB_FILE)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _stash_single_tenant()  # rename old single-server tables aside (if any) BEFORE recreating them
    _conn.executescript(SCHEMA)
    _ensure_columns()  # add columns introduced after a guild_config table was first created
    _conn.commit()
    _adopt_single_tenant()  # copy the stashed rows back in, stamped with the home guild id
    _migrate_ign_multiname()  # old single-name ign -> multi-name (existing name becomes the favorite)
    if migrate:
        _migrate_from_json()


# ---- migration: single-tenant DB -> multi-tenant ---------------------------
def _home_guild_id():
    """The guild that legacy single-server rows belong to - from DISCORD_GUILD_ID. None if unset."""
    try:
        return int(config.GUILD_ID)
    except (TypeError, ValueError):
        return None


def _has_table(name):
    return (
        _db()
        .execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
        .fetchone()
        is not None
    )


def _cols(name):
    return {r[1] for r in _db().execute(f"PRAGMA table_info({name})")}


def _ensure_columns():
    """Add columns introduced in later versions to an existing guild_config (idempotent)."""
    cols = _cols("guild_config")
    if "keep_history" not in cols:
        _db().execute("ALTER TABLE guild_config ADD COLUMN keep_history INTEGER NOT NULL DEFAULT 1")
    if "admin_role_id" not in cols:
        _db().execute("ALTER TABLE guild_config ADD COLUMN admin_role_id INTEGER")


def _stash_single_tenant():
    """If the DB predates multi-tenancy (pinned/signup have no guild_id), rename them aside so the
    new-shape tables can be created, then adopted from the stash."""
    if _has_table("pinned") and "guild_id" not in _cols("pinned"):
        _db().execute("ALTER TABLE pinned RENAME TO _legacy_pinned")
    if _has_table("signup") and "guild_id" not in _cols("signup"):
        _db().execute("ALTER TABLE signup RENAME TO _legacy_signup")
    # old ign had one name per user (no `favorite` column) - stash it for the multi-name migration
    if _has_table("ign") and "favorite" not in _cols("ign"):
        _db().execute("ALTER TABLE ign RENAME TO _legacy_ign")
    _db().commit()


def _migrate_ign_multiname():
    """One-time: bring the old single-name ign table into the multi-name shape, keeping each user's
    existing name as their favorite (so it still shows on the roster)."""
    if not _has_table("_legacy_ign"):
        return
    _db().execute(
        "INSERT OR IGNORE INTO ign(user_id, name, favorite, set_at) "
        "SELECT user_id, name, 1, set_at FROM _legacy_ign"
    )
    _db().execute("DROP TABLE _legacy_ign")
    _db().commit()


def _adopt_single_tenant():
    """Copy stashed single-server rows into the new tables, stamped with the home guild id, and seed
    that guild's config from the env so it keeps posting to the same channel. Drops the stash."""
    if not (_has_table("_legacy_pinned") or _has_table("_legacy_signup")):
        return
    gid = _home_guild_id()
    if gid is None:
        print(
            "MIGRATION: legacy single-tenant data found but DISCORD_GUILD_ID is unset - cannot "
            "attribute it to a guild; dropping it.",
            flush=True,
        )
    if _has_table("_legacy_pinned"):
        if gid is not None:
            row = (
                _db().execute("SELECT message_id, zday FROM _legacy_pinned WHERE id = 1").fetchone()
            )
            if row and (row["message_id"] is not None or row["zday"] is not None):
                _db().execute(
                    "INSERT OR IGNORE INTO pinned(guild_id, message_id, zday) VALUES (?, ?, ?)",
                    (gid, row["message_id"], row["zday"]),
                )
            if config.CHANNEL_ID:
                _db().execute(
                    "INSERT OR IGNORE INTO guild_config"
                    "(guild_id, channel_id, ping_role_id, enabled, created_at) VALUES (?,?,?,1,?)",
                    (gid, config.CHANNEL_ID, config.PING_ROLE_ID or None, _now()),
                )
            print(f"MIGRATION: adopted single-tenant pinned message for guild {gid}", flush=True)
        _db().execute("DROP TABLE _legacy_pinned")
    if _has_table("_legacy_signup"):
        if gid is not None:
            _db().execute(
                "INSERT OR IGNORE INTO signup(guild_id, zday, quest_type, user_id, signed_up_at) "
                "SELECT ?, zday, quest_type, user_id, signed_up_at FROM _legacy_signup",
                (gid,),
            )
        _db().execute("DROP TABLE _legacy_signup")
    _db().commit()


def _migrate_from_json():
    """First-ever startup only: adopt the pinned message id from the legacy state.json so we don't
    post a duplicate. Needs DISCORD_GUILD_ID set to know which guild it belongs to."""
    gid = _home_guild_id()
    if gid is None:
        return
    if _db().execute("SELECT 1 FROM pinned WHERE guild_id = ?", (gid,)).fetchone():
        return
    try:
        with open(config.STATE_FILE) as f:
            data = json.load(f)
    except Exception:
        return
    mid, zday = data.get("message_id"), data.get("zday")
    if mid and zday:
        _db().execute(
            "INSERT OR IGNORE INTO pinned(guild_id, message_id, zday) VALUES (?, ?, ?)",
            (gid, mid, zday),
        )
        if config.CHANNEL_ID:
            _db().execute(
                "INSERT OR IGNORE INTO guild_config"
                "(guild_id, channel_id, ping_role_id, enabled, created_at) VALUES (?,?,?,1,?)",
                (gid, config.CHANNEL_ID, config.PING_ROLE_ID or None, _now()),
            )
        _db().commit()
        print(f"migrated pinned message {mid} (zday {zday}) from state.json", flush=True)


# ---- per-guild configuration (/setup) --------------------------------------
def set_guild_config(guild_id, channel_id, ping_role_id=None):
    """Create or update a guild's setup and (re)enable it (caller holds `lock`)."""
    _db().execute(
        "INSERT INTO guild_config(guild_id, channel_id, ping_role_id, enabled, created_at) "
        "VALUES (?, ?, ?, 1, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET "
        "channel_id = excluded.channel_id, ping_role_id = excluded.ping_role_id, enabled = 1",
        (guild_id, channel_id, ping_role_id, _now()),
    )
    _db().commit()


def get_guild_config(guild_id):
    """A guild's config row (guild_id, channel_id, ping_role_id, admin_role_id, enabled,
    keep_history), or None."""
    return (
        _db()
        .execute(
            "SELECT guild_id, channel_id, ping_role_id, admin_role_id, enabled, keep_history "
            "FROM guild_config WHERE guild_id = ?",
            (guild_id,),
        )
        .fetchone()
    )


def configured_guilds():
    """All guilds the bot should actively post in (enabled + have a channel)."""
    return (
        _db()
        .execute(
            "SELECT guild_id, channel_id, ping_role_id, enabled, keep_history FROM guild_config "
            "WHERE enabled = 1 AND channel_id IS NOT NULL"
        )
        .fetchall()
    )


def disable_guild(guild_id):
    """Stop posting in a guild without forgetting its data (caller holds `lock`). True if it existed."""
    cur = _db().execute("UPDATE guild_config SET enabled = 0 WHERE guild_id = ?", (guild_id,))
    _db().commit()
    return cur.rowcount > 0


def enable_guild(guild_id):
    """Resume a previously-configured guild using its saved channel (caller holds `lock`). Returns
    "enabled" if it was turned back on, "already" if it was already active, or "absent" if the guild
    has no usable config to resume (run /setup first)."""
    row = get_guild_config(guild_id)
    if row is None or row["channel_id"] is None:
        return "absent"
    if row["enabled"]:
        return "already"
    _db().execute("UPDATE guild_config SET enabled = 1 WHERE guild_id = ?", (guild_id,))
    _db().commit()
    return "enabled"


def set_history_retention(guild_id, keep):
    """Turn keeping of past days' signups on/off for a guild (caller holds `lock`). Returns "absent"
    if the guild has no config, "already" if it's unchanged, or "changed"."""
    row = get_guild_config(guild_id)
    if row is None:
        return "absent"
    if bool(row["keep_history"]) == bool(keep):
        return "already"
    _db().execute(
        "UPDATE guild_config SET keep_history = ? WHERE guild_id = ?",
        (1 if keep else 0, guild_id),
    )
    _db().commit()
    return "changed"


def set_admin_role(guild_id, role_id):
    """Set (role_id) or clear (None) the role allowed to run admin commands besides Manage Server
    (caller holds `lock`). Upserts so it works even before /setup."""
    _db().execute(
        "INSERT INTO guild_config(guild_id, admin_role_id, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET admin_role_id = excluded.admin_role_id",
        (guild_id, role_id, _now()),
    )
    _db().commit()


def clear_history(guild_id):
    """Delete a guild's stored PAST-day signups, leaving today's live roster intact (caller holds
    `lock`). Returns how many past days were cleared."""
    today = zaishen.zaishen_day().isoformat()
    days = [
        r[0]
        for r in _db().execute(
            "SELECT DISTINCT zday FROM signup WHERE guild_id = ? AND zday < ?", (guild_id, today)
        )
    ]
    _db().execute("DELETE FROM signup WHERE guild_id = ? AND zday < ?", (guild_id, today))
    _db().commit()
    return len(days)


def delete_guild(guild_id):
    """Purge ALL of a guild's data - on removal from the server (caller holds `lock`). Leaves the
    global `ign` table alone, since a GW1 name is a user attribute shared across servers."""
    for t in ("signup", "pinned", "guild_config"):
        _db().execute(f"DELETE FROM {t} WHERE guild_id = ?", (guild_id,))
    _db().commit()


# ---- the pinned message (per guild) ----------------------------------------
def message_id(guild_id):
    row = _db().execute("SELECT message_id FROM pinned WHERE guild_id = ?", (guild_id,)).fetchone()
    return row["message_id"] if row else None


def set_message_id(guild_id, mid):
    """Record the id of the guild's currently pinned message (caller holds `lock`)."""
    today = zaishen.zaishen_day().isoformat()
    _db().execute(
        "INSERT INTO pinned(guild_id, message_id, zday) VALUES (?, ?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET message_id = excluded.message_id, zday = excluded.zday",
        (guild_id, mid, today),
    )
    _db().commit()


def begin_day_rollover(guild_id):
    """For the keep-at-bottom loop (caller holds `lock`): if a new Zaishen day started for this guild,
    record the day's quests and clear the pinned slot so a fresh post is made. Returns
    (new_day, message_id, first_ever) where message_id is the EXISTING/previous message (on a new day
    that's yesterday's post, which the caller deletes when posting the new one so it doesn't linger).
    first_ever is True when the guild has no pinned row yet, so the caller can skip the daily ping on
    the very first post."""
    today = zaishen.zaishen_day().isoformat()
    row = (
        _db()
        .execute("SELECT message_id, zday FROM pinned WHERE guild_id = ?", (guild_id,))
        .fetchone()
    )
    first_ever = row is None
    cur_zday = row["zday"] if row else None
    mid = row["message_id"] if row else None
    new_day = cur_zday != today
    if new_day:
        ensure_daily(today)
        # honor the guild's retention choice: if it doesn't keep history, drop past days' signups
        gc = get_guild_config(guild_id)
        if gc is not None and not gc["keep_history"]:
            _db().execute("DELETE FROM signup WHERE guild_id = ? AND zday < ?", (guild_id, today))
        _db().execute(
            "INSERT INTO pinned(guild_id, message_id, zday) VALUES (?, NULL, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET message_id = NULL, zday = excluded.zday",
            (guild_id, today),
        )
        _db().commit()
        # keep `mid` = the previous (yesterday's) message id so the caller can delete it; the DB slot
        # is now NULL, marking that a fresh message must be posted.
    return new_day, mid, first_ever


# ---- the day's quests (recorded for history / queries; global) -------------
def ensure_daily(zday):
    """Record the four computed quests for `zday` if not already stored (idempotent)."""
    day = date.fromisoformat(zday)
    rows = [(zday, qt, name) for qt, _emoji, _label, name in zaishen.all_quests(day)]
    _db().executemany(
        "INSERT OR IGNORE INTO daily(zday, quest_type, quest_name) VALUES (?, ?, ?)", rows
    )
    _db().commit()


def dailies(zday):
    """Recorded quests for `zday` as {quest_type: quest_name} (mainly for history/queries)."""
    return {
        r["quest_type"]: r["quest_name"]
        for r in _db().execute("SELECT quest_type, quest_name FROM daily WHERE zday = ?", (zday,))
    }


# ---- per-mission signups (per guild) ---------------------------------------
def signups(guild_id, zday):
    """{quest_type: [user_id, ...]} for one guild + day, each list ordered by sign-up time."""
    out = {qt: [] for qt, _emoji, _label in zaishen.QUEST_TYPES}
    for r in _db().execute(
        "SELECT quest_type, user_id FROM signup WHERE guild_id = ? AND zday = ? "
        "ORDER BY signed_up_at, rowid",
        (guild_id, zday),
    ):
        out.setdefault(r["quest_type"], []).append(r["user_id"])
    return out


def toggle(guild_id, zday, quest_type, uid):
    """Toggle a user's signup for one quest (caller holds `lock`). Returns True if now signed up."""
    found = (
        _db()
        .execute(
            "SELECT 1 FROM signup WHERE guild_id = ? AND zday = ? AND quest_type = ? AND user_id = ?",
            (guild_id, zday, quest_type, uid),
        )
        .fetchone()
    )
    if found:
        _db().execute(
            "DELETE FROM signup WHERE guild_id = ? AND zday = ? AND quest_type = ? AND user_id = ?",
            (guild_id, zday, quest_type, uid),
        )
        signed = False
    else:
        _db().execute(
            "INSERT INTO signup(guild_id, zday, quest_type, user_id, signed_up_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, zday, quest_type, uid, _now()),
        )
        signed = True
    _db().commit()
    return signed


def sign_all(guild_id, zday, uid):
    """Sign a user up for all four quests (caller holds `lock`). Idempotent - keeps existing rows."""
    now = _now()
    _db().executemany(
        "INSERT OR IGNORE INTO signup(guild_id, zday, quest_type, user_id, signed_up_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(guild_id, zday, qt, uid, now) for qt, _emoji, _label in zaishen.QUEST_TYPES],
    )
    _db().commit()


def sign_off_all(guild_id, zday, uid):
    """Remove a user from every quest that day (caller holds `lock`)."""
    _db().execute(
        "DELETE FROM signup WHERE guild_id = ? AND zday = ? AND user_id = ?", (guild_id, zday, uid)
    )
    _db().commit()


def signup_history(guild_id, limit_days=7):
    """Recent days that had sign-ups in this guild, newest first. Returns a list of
    (zday, [(quest_type, quest_name, [user_id, ...]), ...]) - only quests that had sign-ups, in
    canonical quest order. Capped to the `limit_days` most recent days with any activity."""
    order = [qt for qt, _emoji, _label in zaishen.QUEST_TYPES]
    by_day = {}
    days = []
    for r in _db().execute(
        "SELECT zday, quest_type, user_id FROM signup WHERE guild_id = ? "
        "ORDER BY zday DESC, signed_up_at, rowid",
        (guild_id,),
    ):
        z = r["zday"]
        if z not in by_day:
            if len(days) >= limit_days:
                continue  # older than the window - skip (rows are newest-day-first)
            by_day[z] = {}
            days.append(z)
        by_day[z].setdefault(r["quest_type"], []).append(r["user_id"])
    out = []
    for z in days:
        names = dailies(z)
        qmap = by_day[z]
        out.append((z, [(qt, names.get(qt, "?"), qmap[qt]) for qt in order if qt in qmap]))
    return out


# ---- IGN (self-declared GW1 character names; global per user) --------------
# A user can register several character names; at most one is the "favorite", which is the only one
# shown next to their handle on the roster. No favorite -> nothing is shown.
def add_ign(uid, name):
    """Add a character name for a user (caller holds `lock`). Does NOT auto-favorite. Returns
    "added", "exists" (already had that name), or "full" (hit MAX_IGNS)."""
    if _db().execute("SELECT 1 FROM ign WHERE user_id = ? AND name = ?", (uid, name)).fetchone():
        return "exists"
    count = _db().execute("SELECT COUNT(*) c FROM ign WHERE user_id = ?", (uid,)).fetchone()["c"]
    if count >= MAX_IGNS:
        return "full"
    _db().execute(
        "INSERT INTO ign(user_id, name, favorite, set_at) VALUES (?, ?, 0, ?)", (uid, name, _now())
    )
    _db().commit()
    return "added"


def remove_ign(uid, name):
    """Remove one of a user's character names (caller holds `lock`). Returns True if it existed."""
    cur = _db().execute("DELETE FROM ign WHERE user_id = ? AND name = ?", (uid, name))
    _db().commit()
    return cur.rowcount > 0


def set_favorite(uid, name):
    """Make `name` the user's favorite (the one shown on the roster), clearing any other (caller
    holds `lock`). Returns False if the user doesn't have that name."""
    if (
        not _db()
        .execute("SELECT 1 FROM ign WHERE user_id = ? AND name = ?", (uid, name))
        .fetchone()
    ):
        return False
    _db().execute("UPDATE ign SET favorite = 0 WHERE user_id = ?", (uid,))
    _db().execute("UPDATE ign SET favorite = 1 WHERE user_id = ? AND name = ?", (uid, name))
    _db().commit()
    return True


def clear_favorite(uid):
    """Unset a user's favorite so nothing shows on the roster (caller holds `lock`). True if one was
    set."""
    cur = _db().execute("UPDATE ign SET favorite = 0 WHERE user_id = ? AND favorite = 1", (uid,))
    _db().commit()
    return cur.rowcount > 0


def clear_igns(uid):
    """Remove ALL of a user's character names (caller holds `lock`). Returns how many were removed."""
    cur = _db().execute("DELETE FROM ign WHERE user_id = ?", (uid,))
    _db().commit()
    return cur.rowcount


def names_for(uid):
    """A user's character names as [(name, is_favorite_bool), ...], favorite first then by name."""
    return [
        (r["name"], bool(r["favorite"]))
        for r in _db().execute(
            "SELECT name, favorite FROM ign WHERE user_id = ? ORDER BY favorite DESC, name COLLATE NOCASE",
            (uid,),
        )
    ]


def favorite_name(uid):
    """A user's favorite character name, or None if they haven't set one."""
    row = (
        _db().execute("SELECT name FROM ign WHERE user_id = ? AND favorite = 1", (uid,)).fetchone()
    )
    return row["name"] if row else None


def favorites_for(uids):
    """Batch: {user_id: favorite_name} for the given users that have a favorite set (for the roster)."""
    uids = list(uids)
    if not uids:
        return {}
    placeholders = ",".join("?" * len(uids))
    return {
        r["user_id"]: r["name"]
        for r in _db().execute(
            f"SELECT user_id, name FROM ign WHERE favorite = 1 AND user_id IN ({placeholders})",
            uids,
        )
    }
