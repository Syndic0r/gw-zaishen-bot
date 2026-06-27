"""
Slash-command groups.

/ign - your GW1 character names (self-declared; no third-party login, since GW1 has no account API).
A GW1 account has several characters, so you can register several names. One can be your "favorite",
which is the only one shown next to your handle on the daily roster. No favorite -> nothing is shown.
  /ign add <name>        register a character name
  /ign remove <name>     remove one of your names
  /ign favorite <name>   choose the name shown on the roster
  /ign unfavorite        show none of your names on the roster
  /ign who [@user]       show all of a player's names (* marks the favorite); defaults to you
  /ign clear             remove all of your names
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


async def _own_name_autocomplete(interaction: discord.Interaction, current: str):
    """Suggest the invoker's own registered names (for remove / favorite)."""
    cur = current.lower()
    return [
        app_commands.Choice(name=("* " + n) if fav else n, value=n)
        for n, fav in storage.names_for(interaction.user.id)
        if cur in n.lower()
    ][:25]


ign = app_commands.Group(name="ign", description="Your GW1 character names")


@ign.command(name="add", description="Register a GW1 character name")
@app_commands.describe(name="A character name on your GW1 account")
async def ign_add(interaction: discord.Interaction, name: str):
    name = _clean_ign(name)
    if not name:
        await interaction.response.send_message(
            "Give a character name, e.g. `/ign add Dervish McStab`.", ephemeral=True
        )
        return
    if len(name) > MAX_IGN_LEN:
        await interaction.response.send_message(
            f"That's a bit long - keep it under {MAX_IGN_LEN} characters.", ephemeral=True
        )
        return
    async with storage.lock:
        result = storage.add_ign(interaction.user.id, name)
        has_fav = storage.favorite_name(interaction.user.id) is not None
    if result == "exists":
        msg = f"You already have **{esc(name)}**."
    elif result == "full":
        msg = f"You've hit the limit of {storage.MAX_IGNS} names - remove one with `/ign remove` first."
    else:
        msg = f"✅ Added **{esc(name)}**."
        if not has_fav:
            msg += " Set it as the one shown on the roster with `/ign favorite`."
    await interaction.response.send_message(msg, ephemeral=True, allowed_mentions=NONE_MENTIONS)


@ign.command(name="remove", description="Remove one of your GW1 character names")
@app_commands.describe(name="The name to remove")
@app_commands.autocomplete(name=_own_name_autocomplete)
async def ign_remove(interaction: discord.Interaction, name: str):
    name = _clean_ign(name)
    async with storage.lock:
        existed = storage.remove_ign(interaction.user.id, name)
    await interaction.response.send_message(
        f"🗑️ Removed **{esc(name)}**." if existed else f"You don't have **{esc(name)}**.",
        ephemeral=True,
        allowed_mentions=NONE_MENTIONS,
    )


@ign.command(name="favorite", description="Choose which of your names shows on the roster")
@app_commands.describe(name="The name to show on the daily roster")
@app_commands.autocomplete(name=_own_name_autocomplete)
async def ign_favorite(interaction: discord.Interaction, name: str):
    name = _clean_ign(name)
    async with storage.lock:
        ok = storage.set_favorite(interaction.user.id, name)
    await interaction.response.send_message(
        f"⭐ **{esc(name)}** will now show next to your name on the roster."
        if ok
        else f"You don't have **{esc(name)}** - add it first with `/ign add`.",
        ephemeral=True,
        allowed_mentions=NONE_MENTIONS,
    )


@ign.command(name="unfavorite", description="Show none of your names on the roster")
async def ign_unfavorite(interaction: discord.Interaction):
    async with storage.lock:
        had = storage.clear_favorite(interaction.user.id)
    await interaction.response.send_message(
        "Done - your handle will show without a character name."
        if had
        else "You had no favorite set.",
        ephemeral=True,
    )


@ign.command(name="who", description="Show all of a player's GW1 character names")
@app_commands.describe(user="Whose names to show (defaults to you)")
async def ign_who(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    names = storage.names_for(target.id)
    if not names:
        who = "You have" if target.id == interaction.user.id else f"{esc(target.display_name)} has"
        await interaction.response.send_message(
            f"{who} no GW1 names registered yet"
            + (" - add one with `/ign add`." if target.id == interaction.user.id else "."),
            ephemeral=True,
            allowed_mentions=NONE_MENTIONS,
        )
        return
    lines = [f"{'⭐ ' if fav else '• '}{esc(n)}" for n, fav in names]
    view = PagedList(
        interaction.user.id, lines, title=f"{esc(target.display_name)}'s GW1 names ({len(names)})"
    )
    await interaction.response.send_message(
        view.content(), view=view, ephemeral=True, allowed_mentions=NONE_MENTIONS
    )


@ign.command(name="clear", description="Remove all of your GW1 character names")
async def ign_clear(interaction: discord.Interaction):
    async with storage.lock:
        n = storage.clear_igns(interaction.user.id)
    await interaction.response.send_message(
        f"🗑️ Removed all {n} of your names." if n else "You had no names registered.",
        ephemeral=True,
    )
