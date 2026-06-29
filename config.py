"""
Central configuration: environment variables + shared constants. Everything the bot reads from the
environment (set via the systemd EnvironmentFile, /etc/gw1-zaishen-bot.conf) lives here, so there's
one place to look.

The bot is multi-tenant: each server picks its channel + optional ping role in-app via `/setup`
(stored in the DB), so those are no longer global env vars. The three legacy IDs below remain only
to (a) migrate the original single-server deployment into the DB and (b) speed up dev command sync.

  DISCORD_BOT_TOKEN            (required)  bot token from the Discord Developer Portal
  DISCORD_GUILD_ID             (optional)  a dev/home server ID - gives INSTANT slash-command sync
                                           there, and tells the migration which guild the pre-existing
                                           single-server rows belong to
  DISCORD_ZAISHEN_CHANNEL_ID   (optional)  legacy: the original single-server channel, used once to
                                           seed that guild's /setup config on migration
  DISCORD_PING_ROLE_ID         (optional)  legacy: the original single-server ping role (migration)
  CHECK_INTERVAL               (optional)  seconds between bottom-of-channel re-checks (default 60)
  GUILD_REFRESH_TIMEOUT        (optional)  per-guild refresh timeout in the daily loop (default 30)
  BOT_DB_FILE                  (optional)  SQLite database path (default beside this file)
  BOT_STATE_FILE               (optional)  legacy JSON state, read once to migrate into the DB
"""

import os

import discord


def _int_env(name, default=0):
    raw = os.environ.get(name, "").split("#", 1)[0].strip()  # tolerate a stray inline comment
    try:
        return int(raw)
    except ValueError:
        return default


TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "").split("#", 1)[0].strip()
CHANNEL_ID = _int_env("DISCORD_ZAISHEN_CHANNEL_ID")
PING_ROLE_ID = _int_env("DISCORD_PING_ROLE_ID")
CHECK_INTERVAL = _int_env("CHECK_INTERVAL", 60)
# Upper bound (seconds) on a single guild's refresh in the daily loop, so one slow / rate-limited
# guild can't stall the whole round. Generous - a healthy refresh is well under a second.
GUILD_REFRESH_TIMEOUT = _int_env("GUILD_REFRESH_TIMEOUT", 30)
DB_FILE = os.environ.get("BOT_DB_FILE", "").strip() or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "gw1.db"
)
# legacy JSON state - read once on first SQLite startup to migrate the pinned message id
STATE_FILE = os.environ.get("BOT_STATE_FILE", "").strip() or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "state.json"
)

# allowed-mention policies, shared by the renderer / views / keep-at-bottom loop
NONE_MENTIONS = discord.AllowedMentions.none()
ROLE_MENTIONS = discord.AllowedMentions(roles=True, users=False, everyone=False)
