"""Cross-server ranked: ONE match shown in TWO channels (they can be in different servers).

How it works
------------
* MirrorChannel looks like a normal text channel to the match code (views.py), but `send()` posts to
  every channel it wraps. Buttons are attached to both copies and both players use their own copy.
* MirrorMessage is what `send()` returns: `edit()` / `delete()` are applied to every copy.
* GameRegistry is `active_games`: a mirrored match is stored under BOTH channel ids, and removing it
  under either id removes both, so every existing `active_games.pop(channel.id, None)` keeps working.
* Mentions of a player who is NOT a member of the target server are shown as a bold name there
  (Discord can't resolve such mentions).

Only ranked player-vs-player matches use this. Normal `rcmp @user` matches never touch this file.
"""

from __future__ import annotations

import asyncio
import io

import discord

_tasks: set = set()   # keeps the button-cleanup tasks alive until they finish


# ── active_games ─────────────────────────────────────────────────────────────

class GameRegistry(dict):
    """channel id -> game. A mirrored game lives under every channel id in `game.mirror_ids`;
    removing it under one id removes it under all of them."""

    @staticmethod
    def _ids(game):
        return getattr(game, "mirror_ids", None) or ()

    def pop(self, key, *default):
        game = dict.get(self, key)
        res = dict.pop(self, key, *default)
        for k in self._ids(game):
            if dict.get(self, k) is game:
                dict.pop(self, k, None)
        return res

    def __delitem__(self, key):
        game = dict.get(self, key)
        dict.__delitem__(self, key)
        for k in self._ids(game):
            if dict.get(self, k) is game:
                dict.pop(self, k, None)


# ── text helpers ─────────────────────────────────────────────────────────────

def _mention_map(members, guild_id) -> dict:
    """{'<@id>': '**Name**'} for every player who is not a member of `guild_id`."""
    out: dict = {}
    for who in members:
        g = getattr(who, "guild", None)
        if g is None or g.id == guild_id:
            continue
        try:
            name = discord.utils.escape_markdown(str(getattr(who, "display_name", "player")))
        except Exception:
            name = str(getattr(who, "display_name", "player"))
        out[f"<@{who.id}>"] = f"**{name}**"
        out[f"<@!{who.id}>"] = f"**{name}**"
    return out


def _apply(text, mp: dict):
    if not text or not mp or not isinstance(text, str):
        return text
    for k, v in mp.items():
        text = text.replace(k, v)
    return text


def _localize_embed(embed, mp: dict):
    if embed is None or not mp:
        return embed
    try:
        d = embed.to_dict()
        for key in ("title", "description"):
            if key in d:
                d[key] = _apply(d[key], mp)
        for f in d.get("fields", []) or []:
            f["name"] = _apply(f.get("name"), mp)
            f["value"] = _apply(f.get("value"), mp)
        if isinstance(d.get("footer"), dict) and "text" in d["footer"]:
            d["footer"]["text"] = _apply(d["footer"]["text"], mp)
        if isinstance(d.get("author"), dict) and "name" in d["author"]:
            d["author"]["name"] = _apply(d["author"]["name"], mp)
        return discord.Embed.from_dict(d)
    except Exception:
        return embed


def _snapshot_files(file, files) -> list:
    """discord.File objects can be sent only once, so read their bytes now and rebuild a fresh
    File for every channel."""
    all_files = list(files or [])
    if file is not None:
        all_files.append(file)
    snaps = []
    for f in all_files:
        try:
            try:
                f.reset()
            except Exception:
                pass
            data = f.fp.read()
            snaps.append((data, f.filename, bool(getattr(f, "spoiler", False)), getattr(f, "description", None)))
        except Exception as e:
            print(f"[mirror] could not copy attachment: {type(e).__name__}: {e}")
    return snaps


def _fresh_files(snaps) -> list:
    out = []
    for data, name, spoiler, desc in snaps:
        try:
            out.append(discord.File(io.BytesIO(data), filename=name, spoiler=spoiler, description=desc))
        except TypeError:   # very old discord.py without `description`
            out.append(discord.File(io.BytesIO(data), filename=name, spoiler=spoiler))
    return out


def _forget_view(view, messages) -> None:
    """discord.py only forgets a view under the LAST message it was sent with. Without this, the
    other copy's entries (and through them the whole game object) would stay in memory forever."""
    try:
        store = messages[0]._state._view_store
        for m in messages:
            view._cache_key = m.id
            store.remove_view(view)
    except Exception:
        pass


async def _after_view(view, messages) -> None:
    """When a view ends (button used / stopped / timed out), take the buttons off every copy so the
    other player's channel doesn't keep dead buttons."""
    try:
        await view.wait()
    except Exception:
        return
    for m in messages:
        try:
            await m.edit(view=None)
        except Exception:
            pass
    _forget_view(view, messages)


# ── the mirror ───────────────────────────────────────────────────────────────

class MirrorMessage:
    """The result of MirrorChannel.send(): one logical message that exists in several channels."""

    def __init__(self, mirror: "MirrorChannel", messages: list):
        self._mirror = mirror
        self.messages = messages

    @property
    def id(self):
        return self.messages[0].id

    def __getattr__(self, name):   # content, embeds, jump_url ... come from the first copy
        if name in ("messages", "_mirror"):
            raise AttributeError(name)
        return getattr(self.messages[0], name)

    async def edit(self, **kwargs):
        async def one(m):
            kw = dict(kwargs)
            guild = getattr(getattr(m, "channel", None), "guild", None)
            mp = self._mirror._map_for(guild)
            if "content" in kw:
                kw["content"] = _apply(kw["content"], mp)
            if kw.get("embed") is not None:
                kw["embed"] = _localize_embed(kw["embed"], mp)
            if kw.get("embeds"):
                kw["embeds"] = [_localize_embed(e, mp) for e in kw["embeds"]]
            return await m.edit(**kw)

        res = await asyncio.gather(*(one(m) for m in self.messages), return_exceptions=True)
        ok = [r for r in res if not isinstance(r, BaseException)]
        if not ok:
            raise res[0]
        return ok[0]

    async def delete(self, **kwargs):
        await asyncio.gather(*(m.delete(**kwargs) for m in self.messages), return_exceptions=True)


class MirrorChannel:
    """Drop-in for a TextChannel in the match code. `id` / `guild` are the first channel's."""

    def __init__(self, channels, members=()):
        self.channels = list(channels)
        self.members = list(members)
        self.id = self.channels[0].id
        self.guild = getattr(self.channels[0], "guild", None)

    def __getattr__(self, name):   # mention, name, typing() ... come from the first channel
        if name in ("channels", "members"):
            raise AttributeError(name)
        return getattr(self.channels[0], name)

    def __repr__(self) -> str:
        return f"<MirrorChannel {[c.id for c in self.channels]}>"

    def _map_for(self, guild) -> dict:
        return _mention_map(self.members, getattr(guild, "id", None))

    async def send(self, content=None, *, embed=None, embeds=None, file=None, files=None, **kwargs):
        snaps = _snapshot_files(file, files)

        async def one(ch):
            mp = self._map_for(getattr(ch, "guild", None))
            kw = dict(kwargs)
            if content is not None:
                kw["content"] = _apply(content, mp)
            if embed is not None:
                kw["embed"] = _localize_embed(embed, mp)
            if embeds:
                kw["embeds"] = [_localize_embed(e, mp) for e in embeds]
            if snaps:
                kw["files"] = _fresh_files(snaps)
            return await ch.send(**kw)

        res = await asyncio.gather(*(one(ch) for ch in self.channels), return_exceptions=True)
        sent = []
        for ch, r in zip(self.channels, res):
            if isinstance(r, BaseException):
                print(f"[mirror] send to channel {getattr(ch, 'id', '?')} failed: {type(r).__name__}: {r}")
            else:
                sent.append(r)
        if not sent:
            raise res[0]

        mm = MirrorMessage(self, sent)
        view = kwargs.get("view")
        if view is not None and len(sent) > 0:
            try:
                view.message = mm   # so `view.message.edit(...)` updates every copy
            except Exception:
                pass
            if not view.is_finished():
                t = asyncio.create_task(_after_view(view, sent))
                _tasks.add(t)
                t.add_done_callback(_tasks.discard)
        return mm
