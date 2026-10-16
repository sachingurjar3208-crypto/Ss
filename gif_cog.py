"""Owner-only commentary-GIF commands (link-only storage).

    /addgif key:<word> url:<link> [player:<name>]
        no player  -> UNIVERSAL gif (anyone)
        with player -> only that player
        Add as many gifs per key as you like — one is picked at random.
    /listgif [key] [player]     see saved gifs with their ID
    /removegif id:<number>      delete one gif (ID from /listgif)
    /gifkeys                    show every key word you can use

`key` is plain text. A wrong word gives an error that lists the right ones.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import card_db
import gif_store as gs
from bot_guard import admin_only


def is_owner():
    return admin_only()


class GifCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _bad_key(raw: str) -> str:
        return f"❌ `{raw}` is not a valid key.\n\n{gs.keys_help()}"

    @staticmethod
    def _resolve_player(raw: str) -> tuple[str | None, str | None]:
        """(canonical name, error). Name must exist as a card so typos are caught."""
        raw = raw.strip()
        card = card_db.get_card(raw)
        if card is not None:
            return card["playername"], None
        near = card_db.find_cards(raw, limit=5)
        if near:
            return None, "❌ No exact player **%s**. Did you mean: %s" % (
                raw, ", ".join(f"`{r['playername']}`" for r in near))
        return None, f"❌ No player named **{raw}** in the card database."

    @app_commands.command(name="addgif", description="Add a commentary GIF link (universal, or for one player)")
    @app_commands.describe(
        key="Word like sweep, four, six, 50, 100, wicket ... (see /gifkeys)",
        url="GIF link (Giphy / Tenor / Imgur)",
        player="Leave empty for a universal GIF, or type a player name",
    )
    @is_owner()
    async def addgif(self, interaction: discord.Interaction, key: str, url: str, player: str | None = None):
        k = gs.normalize_key(key)
        if k is None:
            await interaction.response.send_message(self._bad_key(key), ephemeral=True)
            return
        link = gs.normalize_gif_url(url)
        if link is None:
            await interaction.response.send_message("❌ `url` must be a link starting with http:// or https://", ephemeral=True)
            return
        pname = None
        if player and player.strip():
            pname, err = self._resolve_player(player)
            if err:
                await interaction.response.send_message(err, ephemeral=True)
                return
        added = gs.add_gif(k, link, pname)
        who = f"**{pname}**" if pname else "**everyone** (universal)"
        if not added:
            await interaction.response.send_message(f"ℹ️ That link is already saved for `{k}` → {who}.", ephemeral=True)
            return
        n = len(gs.list_gifs(k, pname, universal_only=not pname))
        embed = discord.Embed(title="✅ GIF added", description=f"Key `{k}` → {who}\nTotal for this: **{n}**",
                              color=discord.Color.green())
        embed.set_image(url=link)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="listgif", description="Show saved commentary GIFs")
    @app_commands.describe(key="Only this key (optional)", player="Only this player (optional)")
    @is_owner()
    async def listgif(self, interaction: discord.Interaction, key: str | None = None, player: str | None = None):
        k = None
        if key and key.strip():
            k = gs.normalize_key(key)
            if k is None:
                await interaction.response.send_message(self._bad_key(key), ephemeral=True)
                return
        rows = gs.list_gifs(k, player.strip() if player and player.strip() else None)
        if not rows:
            await interaction.response.send_message("No GIFs saved for that filter.", ephemeral=True)
            return
        lines = [f"`#{r['id']}` **{r['key']}** · {r['player_key'] or 'universal'} · <{r['url']}>" for r in rows]
        text, chunks, cur = "", [], ""
        for ln in lines:
            if len(cur) + len(ln) + 1 > 1900:
                chunks.append(cur); cur = ""
            cur += ln + "\n"
        chunks.append(cur)
        await interaction.response.send_message(f"🎞️ **{len(rows)} GIF(s)**\n{chunks[0]}", ephemeral=True)
        for extra in chunks[1:4]:
            await interaction.followup.send(extra, ephemeral=True)
        if len(chunks) > 4:
            await interaction.followup.send("…more not shown — filter with `key` or `player`.", ephemeral=True)

    @app_commands.command(name="removegif", description="Delete one commentary GIF by its ID (see /listgif)")
    @app_commands.describe(id="The #number shown in /listgif")
    @is_owner()
    async def removegif(self, interaction: discord.Interaction, id: int):
        row = gs.remove_gif(id)
        if row is None:
            await interaction.response.send_message(f"❌ No GIF with ID `{id}`.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"🗑️ Removed `#{id}` ({row['key']} · {row['player_key'] or 'universal'}).")

    @app_commands.command(name="gifkeys", description="Show every key word you can use for GIFs")
    @is_owner()
    async def gifkeys(self, interaction: discord.Interaction):
        await interaction.response.send_message(gs.keys_help(), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(GifCog(bot))
