"""
Discord UI: the persistent per-mission sign-up buttons on the daily message.

Row 0: one toggle button per daily (🗺️ Mission / 🎯 Bounty / ⚔️ Combat / 💀 Vanquish) - tap to sign
       up for that quest, tap again to sign off just that one.
Row 1: ✅ All (sign up for all four at once) and 🧹 Sign off all (clear yourself from every quest).

All buttons have stable custom_ids so the view is persistent (re-attached on startup via add_view),
i.e. it keeps working across bot restarts.
"""

import discord

import render
import storage
import zaishen
from config import NONE_MENTIONS

QUEST_PREFIX = "zaishen:q:"
ALL_ID = "zaishen:all"
OFFALL_ID = "zaishen:offall"


class PagedList(discord.ui.View):
    """Ephemeral Prev/Next pager over a list of pre-formatted lines; only the invoker can page it."""

    def __init__(self, author_id: int, lines, title="List", per: int = 15):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.title = title
        self.pages = [lines[i : i + per] for i in range(0, len(lines), per)] or [["(none)"]]
        self.page = 0

    def content(self):
        return f"**{self.title}** - page {self.page + 1}/{len(self.pages)}\n" + "\n".join(
            self.pages[self.page]
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This menu isn't yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = (self.page - 1) % len(self.pages)
        await interaction.response.edit_message(
            content=self.content(), view=self, allowed_mentions=NONE_MENTIONS
        )

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def nxt(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = (self.page + 1) % len(self.pages)
        await interaction.response.edit_message(
            content=self.content(), view=self, allowed_mentions=NONE_MENTIONS
        )


class ZaishenView(discord.ui.View):
    """Persistent (timeout=None) view backed by the per-mission roster in storage."""

    def __init__(self):
        super().__init__(timeout=None)
        for qt, emoji, _label in zaishen.QUEST_TYPES:
            btn = discord.ui.Button(
                emoji=emoji,
                label=qt.capitalize(),
                style=discord.ButtonStyle.secondary,
                custom_id=f"{QUEST_PREFIX}{qt}",
                row=0,
            )
            btn.callback = self._quest_cb(qt)
            self.add_item(btn)

        all_btn = discord.ui.Button(
            emoji="✅", label="All", style=discord.ButtonStyle.success, custom_id=ALL_ID, row=1
        )
        all_btn.callback = self._all_cb
        self.add_item(all_btn)

        off_btn = discord.ui.Button(
            emoji="🧹",
            label="Sign off all",
            style=discord.ButtonStyle.secondary,
            custom_id=OFFALL_ID,
            row=1,
        )
        off_btn.callback = self._offall_cb
        self.add_item(off_btn)

    # one toggle callback per quest (closure over the quest type)
    def _quest_cb(self, quest_type):
        async def cb(interaction: discord.Interaction):
            async with storage.lock:
                zday = zaishen.zaishen_day().isoformat()
                storage.ensure_daily(zday)
                storage.toggle(interaction.guild_id, zday, quest_type, interaction.user.id)
            await self._rerender(interaction)

        return cb

    async def _all_cb(self, interaction: discord.Interaction):
        async with storage.lock:
            zday = zaishen.zaishen_day().isoformat()
            storage.ensure_daily(zday)
            storage.sign_all(interaction.guild_id, zday, interaction.user.id)
        await self._rerender(interaction)

    async def _offall_cb(self, interaction: discord.Interaction):
        async with storage.lock:
            zday = zaishen.zaishen_day().isoformat()
            storage.sign_off_all(interaction.guild_id, zday, interaction.user.id)
        await self._rerender(interaction)

    async def _rerender(self, interaction: discord.Interaction):
        # NB: named `_rerender`, not `_refresh` - discord.ui.View has an internal `_refresh()` that it
        # calls during component handling; overriding that name shadowed it (caused a "coroutine never
        # awaited" warning and broke discord's own view refresh).
        # Render fresh AFTER releasing the lock (render reads state synchronously, so it reflects
        # other people's near-simultaneous clicks) and edit in place - this also acks the click.
        try:
            await interaction.response.edit_message(
                content=render.content(interaction.guild_id),
                view=self,
                suppress_embeds=True,
                allowed_mentions=NONE_MENTIONS,
            )
        except Exception as e:
            print(f"toggle edit error: {e!r}", flush=True)
            try:
                await interaction.response.send_message("✅ Updated.", ephemeral=True)
            except Exception:
                pass
