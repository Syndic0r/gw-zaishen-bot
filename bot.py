#!/usr/bin/env python3
"""
Guild Wars 1 Zaishen daily-quest Discord bot - entrypoint.

Multi-tenant: serves many Discord servers at once. Each server's admin runs `/setup #channel` to
choose where the daily message goes (and an optional ping role). The bot posts today's four Zaishen
Challenge Quests (Mission / Bounty / Combat / Vanquish) there and keeps that message pinned to the
BOTTOM of the channel (edits it in place while it's last; deletes + reposts at the bottom if chat
pushed it up). The message carries one toggle button per quest plus "All" / "Sign off all"; the
per-quest rosters are remembered per server in SQLite and shown on the message, resetting at the
16:00 UTC daily reset (when a fresh quest set is posted and the server's optional ping role is
notified once).

Admin commands accept either the Manage Server permission or a per-server bot-admin role (/adminrole).

Slash commands:
  /setup #channel [role] - (admin) choose where to post + optional daily ping role
  /disable               - (admin) pause posting in this server (keeps config + data)
  /enable                - (admin) resume posting with the saved config
  /adminrole [role]      - (Manage Server) set/clear a role allowed to use the admin commands
  /zaishen               - show today's Zaishen dailies (ephemeral; works anywhere)
  /history show [days]   - recent days' sign-ups in this server (ephemeral)
  /history clear|enable|disable - (admin) clear stored history / toggle keeping it
  /ign add|remove|favorite|unfavorite|who|clear - your GW1 character names (the favorite shows on the roster)

This module wires Discord together; the pieces live in:
  config.py    - environment/config + shared constants
  zaishen.py   - the rotation domain (which quests are active)
  storage.py   - persistence (per-guild config, message, per-quest signups, IGNs)
  render.py    - the message text
  views.py     - the buttons + the paged-list helper
  commands.py  - the /ign command group
"""

import asyncio

import discord
from discord import app_commands

import commands
import config
import render
import storage
import zaishen
from config import NONE_MENTIONS, ROLE_MENTIONS
from views import PagedList, ZaishenView

intents = (
    discord.Intents.default()
)  # needs the (non-privileged) guilds intent for join/remove events
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
tree.add_command(commands.ign)  # /ign add | remove | favorite | unfavorite | who | clear

esc = discord.utils.escape_markdown
QT_EMOJI = {qt: emoji for qt, emoji, _label in zaishen.QUEST_TYPES}
_loop_started = False


# ---- keep-at-bottom loop (per guild) ---------------------------------------
async def refresh_guild(gconf):
    """Post / edit / repost ONE guild's daily message so it stays the LAST message in its channel.

    - New Zaishen day → reset shows a fresh roster and a fresh post (pinging the role once, except on
      the guild's very first post).
    - Same day, our message is already last → edit in place.
    - Same day, something pushed it up → delete it and repost at the bottom (roster is kept; it lives
      in storage, not on the message)."""
    guild_id = gconf["guild_id"]
    channel_id = gconf["channel_id"]
    ping_role_id = gconf["ping_role_id"]
    if not channel_id:
        return
    try:
        ch = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
    except discord.NotFound:
        print(f"guild {guild_id}: channel {channel_id} is gone", flush=True)
        return
    except discord.Forbidden:
        print(f"guild {guild_id}: no access to channel {channel_id}", flush=True)
        return
    except Exception as e:
        print(f"guild {guild_id}: channel error: {e!r}", flush=True)
        return

    # Snapshot + handle the daily rollover under the lock - but do NO network here, so a button click
    # (same lock) is never blocked on Discord I/O and can't hit the 3s interaction deadline.
    async with storage.lock:
        new_day, mid, first_ever = storage.begin_day_rollover(guild_id)

    content = render.content(guild_id)  # synchronous read of the latest roster
    view = ZaishenView()

    # is our message still the last one in the channel? (network - lock NOT held)
    last = None
    try:
        async for m in ch.history(limit=1):
            last = m
    except discord.Forbidden:
        print(
            f"guild {guild_id}: missing perms (need View Channel, Send/Read History, Manage Messages)",
            flush=True,
        )
        return
    except Exception as e:
        print(f"guild {guild_id}: history error: {e!r}", flush=True)
        return

    # suppress=True / suppress_embeds=True strip the wiki-link preview embeds so the post stays compact
    try:
        # same day + our message is still last -> edit in place. On a NEW day we always delete the
        # old (yesterday's) message and post a fresh one at the bottom, so old dailies don't pile up.
        if not new_day and mid and last and last.id == mid:
            msg = await ch.fetch_message(mid)
            await msg.edit(
                content=content, view=view, suppress=True, allowed_mentions=NONE_MENTIONS
            )
        else:
            if mid:
                try:
                    await (await ch.fetch_message(mid)).delete()
                except Exception:
                    pass
            # ping the role on a genuine daily rollover - never on the guild's first-ever post
            do_ping = bool(new_day and not first_ever and ping_role_id)
            mentions = ROLE_MENTIONS if do_ping else NONE_MENTIONS
            body = (f"<@&{ping_role_id}>\n" if do_ping else "") + content
            newmsg = await ch.send(body, view=view, allowed_mentions=mentions, suppress_embeds=True)
            async with storage.lock:
                storage.set_message_id(guild_id, newmsg.id)
    except discord.Forbidden:
        print(f"guild {guild_id}: missing permissions to post/edit/delete", flush=True)
    except Exception as e:
        print(f"guild {guild_id}: refresh error: {e!r}", flush=True)


async def daily_loop():
    await client.wait_until_ready()
    while not client.is_closed():
        for gconf in storage.configured_guilds():
            await refresh_guild(gconf)
        await asyncio.sleep(max(15, config.CHECK_INTERVAL))


# ---- permission helpers ----------------------------------------------------
def _is_bot_admin(interaction: discord.Interaction) -> bool:
    """May this member run admin commands? True with Manage Server, or with the server's configured
    bot-admin role (set via /adminrole)."""
    if interaction.user.guild_permissions.manage_guild:
        return True
    gc = storage.get_guild_config(interaction.guild_id)
    role_id = gc["admin_role_id"] if gc else None
    return bool(role_id) and any(r.id == role_id for r in interaction.user.roles)


async def _deny_if_not_admin(interaction: discord.Interaction) -> bool:
    """Reply + return True when the member isn't a bot admin (so the caller can bail out)."""
    if _is_bot_admin(interaction):
        return False
    await interaction.response.send_message(
        "You need the **Manage Server** permission or this server's bot-admin role to do that.",
        ephemeral=True,
    )
    return True


# ---- slash commands --------------------------------------------------------
@tree.command(name="zaishen", description="Show today's Guild Wars Zaishen daily quests")
async def zaishen_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        render.content(interaction.guild_id),
        ephemeral=True,
        suppress_embeds=True,
        allowed_mentions=NONE_MENTIONS,
    )


@tree.command(name="setup", description="Choose the channel for the daily Zaishen message (admin)")
@app_commands.describe(
    channel="Channel to post the daily Zaishen message in",
    ping_role="Optional role to ping once a day when the new quests are posted",
)
@app_commands.guild_only()
async def setup_cmd(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    ping_role: discord.Role = None,
):
    if await _deny_if_not_admin(interaction):
        return
    perms = channel.permissions_for(interaction.guild.me)
    needed = [
        ("View Channel", perms.view_channel),
        ("Send Messages", perms.send_messages),
        ("Embed Links", perms.embed_links),
        ("Read Message History", perms.read_message_history),
        ("Manage Messages", perms.manage_messages),
    ]
    missing = [name for name, ok in needed if not ok]
    if missing:
        await interaction.response.send_message(
            f"I'm missing permissions in {channel.mention}: **{', '.join(missing)}**.\n"
            "Grant those (Manage Messages lets me keep the message pinned to the bottom) and run "
            "`/setup` again.",
            ephemeral=True,
        )
        return

    async with storage.lock:
        storage.set_guild_config(
            interaction.guild_id, channel.id, ping_role.id if ping_role else None
        )
    await interaction.response.send_message(
        f"✅ Set up - I'll post the daily Zaishen quests in {channel.mention}"
        + (f" and ping {ping_role.mention} at each daily reset." if ping_role else ".")
        + " It'll appear in a moment.",
        ephemeral=True,
        allowed_mentions=NONE_MENTIONS,
    )
    # post right away rather than waiting for the next loop tick
    gconf = storage.get_guild_config(interaction.guild_id)
    if gconf:
        try:
            await refresh_guild(gconf)
        except Exception as e:
            print(f"setup refresh error: {e!r}", flush=True)


@tree.command(name="disable", description="Stop posting the daily Zaishen message here (admin)")
@app_commands.guild_only()
async def disable_cmd(interaction: discord.Interaction):
    if await _deny_if_not_admin(interaction):
        return
    async with storage.lock:
        gconf = storage.get_guild_config(interaction.guild_id)
        mid = storage.message_id(interaction.guild_id)
        existed = storage.disable_guild(interaction.guild_id)
    # best-effort: remove the now-orphaned message so it doesn't sit there going stale
    if existed and gconf and gconf["channel_id"] and mid:
        try:
            ch = client.get_channel(gconf["channel_id"]) or await client.fetch_channel(
                gconf["channel_id"]
            )
            await (await ch.fetch_message(mid)).delete()
        except Exception:
            pass
    await interaction.response.send_message(
        "🛑 Stopped posting here. Run `/enable` to resume (or `/setup` to change the channel)."
        if existed
        else "I wasn't set up in this server.",
        ephemeral=True,
    )


@tree.command(name="enable", description="Resume posting the daily Zaishen message here (admin)")
@app_commands.guild_only()
async def enable_cmd(interaction: discord.Interaction):
    if await _deny_if_not_admin(interaction):
        return
    async with storage.lock:
        status = storage.enable_guild(interaction.guild_id)
        gconf = storage.get_guild_config(interaction.guild_id)
    if status == "absent":
        await interaction.response.send_message(
            "This server isn't set up yet - run `/setup #channel` first.", ephemeral=True
        )
        return
    if status == "already":
        await interaction.response.send_message(
            "I'm already posting here. Use `/disable` to stop.", ephemeral=True
        )
        return
    await interaction.response.send_message(
        "▶️ Resumed - the daily Zaishen message will reappear in a moment.",
        ephemeral=True,
        allowed_mentions=NONE_MENTIONS,
    )
    try:
        await refresh_guild(gconf)
    except Exception as e:
        print(f"enable refresh error: {e!r}", flush=True)


@tree.command(
    name="adminrole", description="Set a role allowed to manage me (besides Manage Server)"
)
@app_commands.describe(role="Role that may run my admin commands - leave empty to clear it")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def adminrole_cmd(interaction: discord.Interaction, role: discord.Role = None):
    # only true server admins (Manage Server) decide who else gets admin. default_permissions is just
    # a UI hint a server owner can override, so re-check server-side (and NOT via the bot-admin role -
    # a bot-admin must not be able to promote others).
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            "Only members with **Manage Server** can change the bot-admin role.", ephemeral=True
        )
        return
    async with storage.lock:
        storage.set_admin_role(interaction.guild_id, role.id if role else None)
    await interaction.response.send_message(
        f"✅ Members with {role.mention} can now use my admin commands (Manage Server still works)."
        if role
        else "✅ Cleared the bot-admin role - only Manage Server grants admin now.",
        ephemeral=True,
        allowed_mentions=NONE_MENTIONS,
    )


# ---- /history group --------------------------------------------------------
# Subcommand groups can't set per-subcommand default permissions, so the group is visible to all and
# the mutating subcommands gate themselves (Manage Server or the bot-admin role) at runtime.
history = app_commands.Group(
    name="history", description="Past Zaishen sign-ups in this server", guild_only=True
)


@history.command(name="show", description="Show recent days' Zaishen sign-ups in this server")
@app_commands.describe(days="How many recent days with sign-ups to show (1-30, default 7)")
async def history_show(interaction: discord.Interaction, days: app_commands.Range[int, 1, 30] = 7):
    hist = storage.signup_history(interaction.guild_id, days)
    if not hist:
        await interaction.response.send_message(
            "No sign-ups recorded in this server yet.", ephemeral=True
        )
        return
    igns = storage.favorites_for(
        {u for _z, quests in hist for _qt, _name, ups in quests for u in ups}
    )

    def who(uid):
        name = igns.get(uid)
        return f"<@{uid}> ({esc(name)})" if name else f"<@{uid}>"

    lines = []
    for zday, quests in hist:
        lines.append(f"📅 **{zday}**")
        for qt, name, ups in quests:
            lines.append(f"{QT_EMOJI.get(qt, '•')} {esc(name)} - " + ", ".join(who(u) for u in ups))
    view = PagedList(
        interaction.user.id, lines, title=f"Zaishen sign-up history ({len(hist)} day(s))", per=16
    )
    await interaction.response.send_message(
        view.content(), view=view, ephemeral=True, allowed_mentions=NONE_MENTIONS
    )


@history.command(name="clear", description="Delete stored past sign-ups in this server (admin)")
async def history_clear(interaction: discord.Interaction):
    if await _deny_if_not_admin(interaction):
        return
    async with storage.lock:
        n = storage.clear_history(interaction.guild_id)
    await interaction.response.send_message(
        f"🗑️ Cleared sign-up history ({n} past day(s)). Today's roster is untouched."
        if n
        else "No past sign-up history to clear.",
        ephemeral=True,
    )


@history.command(name="enable", description="Keep past days' sign-ups for /history show (admin)")
async def history_enable(interaction: discord.Interaction):
    if await _deny_if_not_admin(interaction):
        return
    async with storage.lock:
        status = storage.set_history_retention(interaction.guild_id, True)
    await interaction.response.send_message(
        {
            "absent": "Set up the bot first with `/setup #channel`.",
            "already": "History is already being kept.",
            "changed": "✅ I'll keep past days' sign-ups - view them with `/history show`.",
        }[status],
        ephemeral=True,
    )


@history.command(name="disable", description="Stop keeping past sign-ups; purge them daily (admin)")
async def history_disable(interaction: discord.Interaction):
    if await _deny_if_not_admin(interaction):
        return
    async with storage.lock:
        status = storage.set_history_retention(interaction.guild_id, False)
    await interaction.response.send_message(
        {
            "absent": "Set up the bot first with `/setup #channel`.",
            "already": "History is already off.",
            "changed": "✅ I'll stop keeping history - past days are purged at each daily reset. "
            "Use `/history clear` to remove what's already stored.",
        }[status],
        ephemeral=True,
    )


tree.add_command(history)


# ---- guild lifecycle -------------------------------------------------------
@client.event
async def on_guild_join(guild: discord.Guild):
    """Greet a new server and point an admin at /setup. Best-effort - picks the system channel, else
    the first channel the bot can talk in."""
    target = guild.system_channel
    if not (target and target.permissions_for(guild.me).send_messages):
        target = next(
            (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None
        )
    if target:
        try:
            await target.send(
                "👋 Thanks for adding **GW1 Zaishen Bot**!\n"
                "An admin: run `/setup #channel` to choose where I post the daily Zaishen quests "
                "(optionally with a role to ping at each reset).",
                allowed_mentions=NONE_MENTIONS,
            )
        except Exception:
            pass


@client.event
async def on_guild_remove(guild: discord.Guild):
    """Removed from a server → purge that server's data (config, pinned message, sign-ups)."""
    async with storage.lock:
        storage.delete_guild(guild.id)
    print(f"left guild {guild.id}; purged its data", flush=True)


@client.event
async def on_ready():
    global _loop_started
    storage.init()
    client.add_view(ZaishenView())  # re-attach persistent buttons after a restart

    # Global sync (works in every server; ~1h to propagate). If a dev/home guild is configured, also
    # copy the commands there for INSTANT updates while iterating.
    try:
        synced = await tree.sync()
        print(f"Synced {len(synced)} GLOBAL command(s) (may take ~1h to appear)", flush=True)
        if config.GUILD_ID:
            guild = discord.Object(id=int(config.GUILD_ID))
            tree.copy_global_to(guild=guild)
            g = await tree.sync(guild=guild)
            print(
                f"Synced {len(g)} command(s) to dev guild {config.GUILD_ID} (instant)", flush=True
            )
    except Exception as e:
        print(f"COMMAND SYNC ERROR: {e!r}", flush=True)

    if not _loop_started:
        _loop_started = True
        client.loop.create_task(daily_loop())
        print(
            f"Daily loop started (every {max(15, config.CHECK_INTERVAL)}s across configured servers)",
            flush=True,
        )

    print(f"Logged in as {client.user}", flush=True)


if __name__ == "__main__":
    if not config.TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN is not set (see gw-zaishen-bot.conf.example).")
    client.run(config.TOKEN)
