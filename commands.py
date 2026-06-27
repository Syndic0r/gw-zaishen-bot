"""
Slash-command groups.

/ign — link your Discord account to your GW1 character name (self-declared; no third-party login,
since GW1 has no account API). Used to show in-game names next to handles on the daily roster.
  /ign set <name>    link or update your GW1 name
  /ign who [@user]   show a player's linked name (defaults to you)
  /ign clear         remove your linked name
  /ign list          paged list of everyone's linked names
"""

import discord
from discord import app_commands

import storage
from config import NONE_MENTIONS
from views import PagedList

MAX_IGN_LEN = 40  # GW1 character names are short; cap to keep the roster tidy

esc = discord.utils.escape_markdown  # neutralize markdown in user-supplied names on display


def _clean_ign(name):
    """Normalize a user-supplied IGN: collapse whitespace, drop control/zero-width characters."""
    name = " ".join(name.split())
    return "".join(c for c in name if c.isprintable())


ign = app_commands.Group(name="ign", description="Link your Discord to your GW1 character name")


@ign.command(name="set", description="Link (or update) your GW1 character name")
@app_commands.describe(name="Your in-game character name")
async def ign_set(interaction: discord.Interaction, name: str):
    name = _clean_ign(name)
    if not name:
        await interaction.response.send_message(
            "Give a character name, e.g. `/ign set Dervish McStab`.", ephemeral=True
        )
        return
    if len(name) > MAX_IGN_LEN:
        await interaction.response.send_message(
            f"That's a bit long — keep it under {MAX_IGN_LEN} characters.", ephemeral=True
        )
        return
    async with storage.lock:
        storage.set_ign(interaction.user.id, name)
    await interaction.response.send_message(
        f"✅ Linked you to **{esc(name)}**.", ephemeral=True, allowed_mentions=NONE_MENTIONS
    )


@ign.command(name="who", description="Show a player's linked GW1 name")
@app_commands.describe(user="Whose name to show (defaults to you)")
async def ign_who(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    name = storage.get_ign(target.id)
    if name:
        await interaction.response.send_message(
            f"**{esc(target.display_name)}** is **{esc(name)}** in-game.",
            ephemeral=True,
            allowed_mentions=NONE_MENTIONS,
        )
    else:
        who = (
            "You haven't"
            if target.id == interaction.user.id
            else f"{esc(target.display_name)} hasn't"
        )
        await interaction.response.send_message(
            f"{who} linked a GW1 name yet — use `/ign set`.",
            ephemeral=True,
            allowed_mentions=NONE_MENTIONS,
        )


@ign.command(name="clear", description="Remove your linked GW1 name")
async def ign_clear(interaction: discord.Interaction):
    async with storage.lock:
        existed = storage.clear_ign(interaction.user.id)
    await interaction.response.send_message(
        "🗑️ Cleared your linked name." if existed else "You had no linked name.", ephemeral=True
    )


@ign.command(name="list", description="List everyone's linked GW1 names")
async def ign_list(interaction: discord.Interaction):
    rows = storage.all_igns()
    if not rows:
        await interaction.response.send_message(
            "No one has linked a GW1 name yet — be first with `/ign set`.", ephemeral=True
        )
        return
    lines = [f"<@{uid}> — {esc(name)}" for uid, name in rows]
    view = PagedList(interaction.user.id, lines, title=f"Linked GW1 names ({len(rows)})")
    await interaction.response.send_message(
        view.content(), view=view, ephemeral=True, allowed_mentions=NONE_MENTIONS
    )
