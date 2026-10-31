"""
Ball-by-ball commentary.

Look (normal Discord text, no emoji, its OWN message right under the scoreboard):

    ### That's huge! **SIX**
    `0.3` **Holder** to **Rathore**
    Gets under it and upper-cuts it for six.
    -# Rathore moves to 47. 3 runs away from fifty.

  * small monospace chips only for the ball number and the result (`0.5` ... `1 RUN`)
  * a big heading line only for FOUR / SIX / OUT
  * the situation / playstyle / milestone / chase lines are small grey "-#" subtext

Public API (used by views.py / practice_views.py):
    build_ball_commentary(...)  -> str   the block for one ball (same args as before + optional game)
    add_lines(block, *lines)    -> str   add small extra lines (playstyle, milestone, chase)
    pop_animation(game)         -> list  suspense frames for this ball ([] most of the time)
    play_animated(...)          -> bool  shows the frames, then the final block (message edits)

Rules kept from the old version (other code depends on them):
  * A wicket line always contains exactly one dismissal word: BOWLED, LBW, CAUGHT or STUMPED.
    views._dismissal_type() and the DRS view read the dismissal from this text, so NO other
    line may contain "stump", "caught", "catch", "lbw", "plumb" or "in front".
  * The game state is NOT changed here. Everything is text only.
"""

import asyncio
import random
import re

from data import button_label, bat_label

# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────

HEADLINE_BIG = "### "      # Discord heading size for FOUR / SIX / OUT ("## " = bigger, "" = off)
SUBTEXT      = "-# "       # small grey line for situation / playstyle / chase

ANIMATION_ENABLED        = True
ANIM_CHANCE              = {"6": 0.20, "W": 0.25, "4": 0.04}   # chance per ball
ANIM_CLOSE_FINISH_CHANCE = 0.60   # any ball when a chase is down to the wire
ANIM_MIN_GAP_BALLS       = 8      # never two animations within this many balls
ANIM_FRAME_DELAY         = 0.8    # seconds between frames

_RECENT_LIMIT = 16        # no line repeats until this many other lines were used

_PACE_TYPES = ("Fast", "Medium Pacer")

TAGS = {
    "0": "DOT", "1": "1 RUN", "2": "2 RUNS", "3": "3 RUNS", "4": "FOUR", "6": "SIX",
    "W": "OUT", "Wd": "WIDE", "NB": "NO BALL", "NB+1": "NO BALL +1",
}

# Big heading for the big moments (the result word is bolded).
HEADLINES = {
    "6": ["That's huge! **SIX**", "Into the stands! **SIX**", "Out of the ground! **SIX**",
          "What a hit! **SIX**", "All the way! **SIX**"],
    "4": ["Cracking shot! **FOUR**", "Races away! **FOUR**", "Finds the gap! **FOUR**",
          "Too good! **FOUR**"],
    "W": ["Gone! **OUT**", "Big breakthrough! **OUT**", "He has to go! **OUT**",
          "Got him! **OUT**"],
}

_KIND_WORD = {"bowled": "BOWLED", "lbw": "LBW", "caught": "CAUGHT", "stumped": "STUMPED"}

# ─────────────────────────────────────────────────────────────────────────────
# Lines by SHOT  (internal shot name -> outcome -> lines)
#   {b} batter   {bw} bowler   {shot} shot button name   {ball} delivery name
# ─────────────────────────────────────────────────────────────────────────────

SHOT_LINES = {
    "Drive": {
        "4": ["{shot} on the up, through the covers. Four all the way.",
              "Leans into the {ball} and drives. No need to run.",
              "Pure timing. Splits mid-off and cover, and it is gone.",
              "Straight down the ground, past the bowler, to the rope.",
              "Caressed through extra cover. Not a fielder moved."],
        "6": ["Drives on the up and it just keeps going. Over long-off!",
              "Lofted drive, high and handsome over the sight screen.",
              "Down the ground and over the rope. Clean as you like."],
        "3": ["Driven hard, cut off just short of the rope. Three."],
        "2": ["Driven wide of long-off. They come back for two.",
              "Timed into the gap, sharp running. Two."],
        "1": ["Driven to mid-off, they jog through for one.",
              "Firm drive, straight to cover. Just a single.",
              "Pushed into the gap at extra cover. One."],
        "0": ["Drives hard, straight to cover.",
              "Goes for the drive, beaten by the movement.",
              "Firm {shot}, but mid-off is right there."],
    },
    "Lofted": {
        "6": ["{shot}! Clears the front leg and launches it. Huge.",
              "That is massive. Into the second tier.",
              "Swings hard, connects. Deep mid-wicket can only watch it sail.",
              "Gives it everything. Way over long-on."],
        "4": ["Smashed, one bounce and over the rope.",
              "Goes big, not quite the distance, but it races away.",
              "Off the top of the bat, flies over mid-off for four."],
        "3": ["Smashed towards the rope, a great save in the deep. Three."],
        "2": ["Skied, but it drops between two fielders. Two.",
              "Hit into the deep, they hurry back for the second."],
        "1": ["Goes for the {shot}, finds long-on. Just a single.",
              "Big swing, mistimed. They run one."],
        "0": ["Swings hard, misses everything.",
              "{shot} attempt, beaten for pace.",
              "Goes for the big one, finds only air."],
    },
    "Flick": {
        "4": ["Flicked off the pads, fine leg has no chance.",
              "Wristy flick through mid-wicket. Four.",
              "Lovely wrists, whipped away to the rope."],
        "6": ["Flicked, and it keeps going over square leg!",
              "All wrists, all timing, all the way for six."],
        "3": ["Flicked fine, they run hard. Three."],
        "2": ["Flicked into the gap, good running. Two."],
        "1": ["Flicked to square leg for one.",
              "Off the pads, a quick single."],
        "0": ["Flicks, straight to mid-wicket.",
              "Tries to flick, gets nothing on it."],
    },
    "Pull": {
        "4": ["Hooked hard, one bounce into the fence.",
              "Rocks back and hooks. Four.",
              "Short, and punished. Hooked away to the rope."],
        "6": ["Hooked off the eyebrows and over fine leg!",
              "Picks the length early, hooks it into the crowd.",
              "That is a monster {shot}. Out of the ground."],
        "3": ["Hooked fine, a long chase. Three runs."],
        "2": ["Hooked into the gap at deep square. Two."],
        "1": ["Hooks, but finds the deep fielder. One.",
              "Hurried {shot}, falls safe. A single."],
        "0": ["Goes for the {shot}, too quick for him.",
              "Hooks and misses. Thud into the keeper's gloves."],
    },
    "Cut": {
        "4": ["{shot}, over the slips and away. Four.",
              "Slashes at it, flies over point to the rope.",
              "Rides the bounce, {shot} to the third man fence."],
        "6": ["{shot}, all the way over third man!",
              "Gets under it and upper-cuts it for six."],
        "3": ["{shot}, third man runs it down. Three."],
        "2": ["Cut past point, they run two."],
        "1": ["Cut away to deep point for one."],
        "0": ["Tries the {shot}, beaten by the bounce.",
              "Slashes, gets nothing on it."],
    },
    "Sweep": {
        "4": ["Swept hard, fine leg has no chance. Four.",
              "Down on one knee, swept to the rope.",
              "Paddle sweep, perfectly placed. Four."],
        "6": ["Slog sweep, high and long over deep mid-wicket!",
              "Sweeps it, and sweeps it all the way for six."],
        "3": ["Swept, a long chase to the rope. Three."],
        "2": ["Swept fine, back for two."],
        "1": ["Swept to deep square for one."],
        "0": ["Sweeps, misses, hit on the pad. No run.",
              "Sweeps straight to short fine leg."],
    },
    "Reverse-Sweep": {
        "4": ["{shot}ped over short fine leg. Four.",
              "Cheeky {shot}, the keeper can only turn and watch.",
              "{shot}ped fine, races away to the rope."],
        "6": ["{shot}ped over the keeper, and it goes all the way!",
              "Outrageous. {shot}ped for six."],
        "3": ["{shot}ped, fine leg runs a long way. Three."],
        "2": ["{shot}ped, they scamper back for two."],
        "1": ["{shot}ped fine for a single."],
        "0": ["Tries the {shot}, misses completely.",
              "{shot} shot, gets nothing but pad."],
    },
    "Defend": {
        "0": ["Solid {shot}. Dead bat.",
              "Blocked back to the bowler.",
              "Soft hands, the ball dies at his feet.",
              "Watchful {shot}. Nothing given away."],
        "2": ["Blocked, a misfield, and they get two."],
        "1": ["Blocked into the gap, a quick single.",
              "Soft-handed {shot}, they steal one."],
    },
    "Leave": {
        "0": ["{shot}s it. Through to the keeper.",
              "Good judgement, lets it go.",
              "Watches it pass outside off. Well left."],
    },
}

GENERIC_LINES = {
    "6": ["Into the stands! That is a big hit.", "Clears the rope with ease. Six."],
    "4": ["Finds the gap, races to the rope. Four.", "Placed, not hit. Four runs."],
    "3": ["Good running between the wickets. Three."],
    "2": ["Into the gap, back for two."],
    "1": ["Worked away for a single."],
    "0": ["No run.", "Kept out."],
}

# ─────────────────────────────────────────────────────────────────────────────
# Lines by DELIVERY for dot balls (internal delivery -> group)
# ─────────────────────────────────────────────────────────────────────────────

_DELIVERY_GROUP = {
    "Yorker": "yorker", "Full": "full", "Bouncer": "short",
    "Swing": "swing", "Good Length": "length", "Fast": "length",
    "Off Break": "turn", "Leg Break": "turn", "Carrom Ball": "turn",
    "Doosra": "wrong", "Googly": "wrong",
    "Arm Ball": "straight", "Flipper": "straight", "Slider": "straight",
    "Top Spin": "straight", "Top Spinner": "straight", "Drift Ball": "straight",
}

DOT_BY_DELIVERY = {
    "yorker":   ["{ball} at the base, dug out just in time.",
                 "Right in the blockhole. Nothing to do with that."],
    "full":     ["{ball}, but he cannot get it away.",
                 "Full and straight, driven to mid-on."],
    "short":    ["Short, sways out of the line.",
                 "{ball}, too sharp to hit."],
    "swing":    ["Swings away late, beats the edge.",
                 "Shapes back in, kept out late."],
    "length":   ["Good length, nothing to hit.",
                 "Hits the deck hard, kept out."],
    "turn":     ["Turns past the outside edge.",
                 "Big turn, beaten all ends up."],
    "wrong":    ["{ball}, goes the other way. Beaten.",
                 "He did not read the {ball}. Beaten."],
    "straight": ["Skids on, kept out late.",
                 "Dips on him, defended."],
}

# ─────────────────────────────────────────────────────────────────────────────
# Wickets.  Each line carries exactly ONE dismissal word (see module doc).
# ─────────────────────────────────────────────────────────────────────────────

WICKET_LINES = {
    "bowled":  ["BOWLED. Through the gate, off pole gone.",
                "BOWLED him! The timber is everywhere.",
                "BOWLED. Plays all around it, the bails are off.",
                "BOWLED. {ball} beats the bat and hits the woodwork."],
    "lbw":     ["LBW. Struck on the pad, that is plumb.",
                "Trapped on the crease. LBW.",
                "LBW. Pinned, the finger goes up."],
    "caught":  ["CAUGHT. Edged, and the keeper takes it.",
                "CAUGHT in the deep. The {shot} finds the fielder.",
                "Top edge, and CAUGHT at fine leg.",
                "CAUGHT. Spoons it straight up, easy take."],
    "stumped": ["STUMPED. Down the track, beaten, bails off in a flash.",
                "Lured out, misses it. STUMPED.",
                "STUMPED. Way out of his crease, the keeper does the rest."],
}

WICKET_FLAVOUR = {
    "yorker":   "{ball}, right on the money.",
    "short":    "{ball}, and it does the job.",
    "swing":    "Late swing does him.",
    "wrong":    "{ball}. He never picked it.",
    "turn":     "Turn and bounce, too good.",
    "straight": "It did not turn. That is the trap.",
}

EXTRA_LINES = {
    "Wd":   ["WIDE. Sprayed down leg.", "WIDE. Too far outside off.", "WIDE. Loses the line."],
    "NB":   ["NO BALL. Overstepped. Free hit coming up.", "NO BALL. Free hit next ball."],
    "NB+1": ["NO BALL, and they run a single. Free hit next.",
             "NO BALL, plus one off the bat. Free hit coming."],
}

# Dismissal choice: (shot group) -> weights.  Delivery may override below.
_KIND_WEIGHTS_BY_SHOT = {
    "Lofted":        {"caught": 75, "bowled": 15, "lbw": 10},
    "Pull":          {"caught": 75, "bowled": 15, "lbw": 10},
    "Drive":         {"caught": 55, "bowled": 25, "lbw": 20},
    "Cut":           {"caught": 70, "bowled": 20, "lbw": 10},
    "Flick":         {"caught": 40, "lbw": 40, "bowled": 20},
    "Sweep":         {"lbw": 45, "caught": 35, "bowled": 20},
    "Reverse-Sweep": {"lbw": 40, "caught": 40, "bowled": 20},
    "Defend":        {"bowled": 40, "lbw": 35, "caught": 25},
}

# Animation suspense text (frames only, never parsed)
_ANIM_TEXT = {
    "6":       ["{shot}... it's in the air", "{shot}... it's in the air... is it enough?"],
    "4":       ["{shot}... the fielder gives chase", "{shot}... the fielder gives chase... he dives..."],
    "bowled":  ["Plays all around it...", "Plays all around it... listen to that..."],
    "lbw":     ["Hits the pad... big appeal...", "Hits the pad... big appeal... umpire thinks..."],
    "caught":  ["In the air...", "In the air... he is under it..."],
    "stumped": ["Down the track... beaten...", "Down the track... beaten... the keeper..."],
    "close":   ["Here it comes...", "Here it comes... {shot}..."],
}


# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────

_EMOJI_RE = re.compile(
    r"<a?:\w+:\d+>"                      # custom Discord emoji
    r"|[\U0001F000-\U0001FFFF]"          # pictographs, emoticons, flags...
    r"|[☀-➿⬀-⯿]"     # misc symbols / dingbats / arrows-stars
    r"|[️‍⃣]"             # variation selectors, joiners, keycaps
)


def clean(text: str) -> str:
    """Plain text: no emoji, no markdown, no backticks."""
    t = _EMOJI_RE.sub("", str(text or ""))
    t = t.replace("**", "").replace("__", "").replace("`", "'")
    t = t.replace("*", "")
    return re.sub(r"[ \t]+", " ", t).strip()


def _esc(text: str) -> str:
    """Escape Discord markdown in names (so 'A_B' or 'x*y' don't break the formatting)."""
    return re.sub(r"([\\*_~|`>])", r"\\\1", str(text or ""))


def _short(name: str) -> str:
    try:
        from moments import short_name
        return short_name(name) or str(name)
    except Exception:
        return str(name or "")


def _pick(game, pool: list[str]) -> str:
    """Random line, avoiding the last few used in this match."""
    if not pool:
        return ""
    if game is None:
        return random.choice(pool)
    recent = game.__dict__.setdefault("_comm_recent", [])
    fresh = [p for p in pool if p not in recent] or pool
    line = random.choice(fresh)
    recent.append(line)
    del recent[:-_RECENT_LIMIT]
    return line


def _ball_no(game) -> str:
    if game is None:
        return ""
    try:
        lb = int(game.current_legal_balls)
        return f"{lb // 6}.{lb % 6 + 1}"
    except Exception:
        return ""


def _header(game, bowler: str, batter: str, tag: str | None) -> str:
    """`0.5` **Holder** to **Rathore**  `1 RUN`   (tag chip left out when a headline shows it)"""
    ball = _ball_no(game)
    parts = [f"`{ball}`"] if ball else []
    parts.append(f"**{_esc(clean(_short(bowler)))}** to **{_esc(clean(_short(batter)))}**")
    line = " ".join(parts)
    if tag:
        line += f"  `{tag}`"
    return line


def _render(header: str, body: list[str], headline: str | None = None) -> str:
    lines = ([HEADLINE_BIG + headline] if headline else []) + [header] + [b for b in body if b]
    return "\n".join(lines)


def add_lines(block: str, *lines: str) -> str:
    """Add small grey lines (playstyle / milestone / chase / free hit) under the commentary."""
    extra = [SUBTEXT + _esc(clean(ln)) for ln in lines if clean(ln)]
    if not extra:
        return block
    return block + "\n" + "\n".join(extra)


def _fill(line: str, **kw) -> str:
    try:
        return line.format(**kw)
    except Exception:
        return line


# ─────────────────────────────────────────────────────────────────────────────
# Situation line (at most one per ball)
# ─────────────────────────────────────────────────────────────────────────────

def _situation(game, *, outcome, batter, bowler, bowling_type, speed, shot_quality, kind):
    if game is None:
        return ""
    b, bw = _short(batter), _short(bowler)
    runs_in = {"1": 1, "2": 2, "3": 3, "4": 4, "6": 6}.get(outcome, 0)
    try:
        lines: list[str] = []

        if outcome == "W":
            streak = game.__dict__.get("hattrick_streak", {}).get(f"{game.innings}:{bowler}", 0)
            wk_after = game.bowler_stats.get(bowler, {}).get("wickets", 0) + 1
            pship = int(getattr(game, "partnership_runs", 0))
            if streak == 1:
                lines.append(f"Two in two for {bw}. Hat-trick ball next.")
            elif pship >= 50:
                lines.append(f"That ends a {pship}-run stand.")
            elif wk_after >= 3:
                lines.append(f"{bw} now has {wk_after} wickets.")
            elif shot_quality == "bad":
                lines.append("Wrong shot for that ball.")
            if lines:
                return lines[0]

        if runs_in:
            before = game.batsman_stats.get(batter, {}).get("runs", 0)
            after = before + runs_in
            for target, word in ((50, "fifty"), (100, "a hundred")):
                # only when he first reaches 45-49 (or 95-99), and again on 49 / 99
                if (before < target - 5 <= after < target) or (after == target - 1 and before < after):
                    left = target - after
                    unit = "run" if left == 1 else "runs"
                    return f"{b} moves to {after}. {left} {unit} away from {word}."
            p_before = int(getattr(game, "partnership_runs", 0))
            p_after = p_before + runs_in
            for mark in (100, 50):
                if p_before < mark <= p_after:
                    return f"That brings up the {mark}-run stand."
            if outcome in ("4", "6") and shot_quality == "good" and random.random() < 0.5:
                return "Read the length early. Perfect shot choice."

        if outcome == "0":
            dots = int(getattr(game, "consecutive_dots", 0)) + 1
            if dots >= 3 and random.random() < 0.7:
                words = {3: "Three", 4: "Four", 5: "Five", 6: "Six"}.get(dots, str(dots))
                return f"{words} dots in a row. The pressure is building."

        if (outcome in ("0", "W") and bowling_type in _PACE_TYPES
                and speed and speed >= 148 and random.random() < 0.5):
            return f"{speed:.1f} kph. That is serious pace."
    except Exception as e:  # never let a text line break the ball
        print(f"[commentary] situation skipped ({type(e).__name__}: {e})")
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Animation (rare)
# ─────────────────────────────────────────────────────────────────────────────

def _close_finish(game) -> bool:
    try:
        if game.innings != 2:
            return False
        needed = game.runs[0] + 1 - game.runs[1]
        balls_left = game.overs * 6 - game.current_legal_balls
        return 0 < needed <= 6 and 0 < balls_left <= 3
    except Exception:
        return False


def _choose_animation(game, outcome, kind, header_no_tag, shot) -> list[str]:
    if game is None or not ANIMATION_ENABLED:
        return []
    try:
        stamp = game.innings * 10000 + int(game.current_legal_balls)
        last = game.__dict__.get("_anim_last", -10**9)
        if stamp - last < ANIM_MIN_GAP_BALLS:
            return []
        close = _close_finish(game)
        chance = ANIM_CLOSE_FINISH_CHANCE if close else ANIM_CHANCE.get(outcome, 0.0)
        if random.random() >= chance:
            return []
        key = kind if outcome == "W" else outcome
        if key not in _ANIM_TEXT:
            key = "close" if close else None
        if not key:
            return []
        game.__dict__["_anim_last"] = stamp
        return [_render(header_no_tag, [f"*{_esc(clean(_fill(t, shot=shot)))}*"]) for t in _ANIM_TEXT[key]]
    except Exception as e:
        print(f"[commentary] animation skipped ({type(e).__name__}: {e})")
        return []


def pop_animation(game) -> list[str]:
    """Frames prepared for the ball just built (then cleared). [] most of the time."""
    if game is None:
        return []
    return game.__dict__.pop("_anim_frames", None) or []


async def play_animated(channel, content, embed, frames, interaction=None) -> bool:
    """Show the suspense frames, then the final commentary + embed.
    Returns True when the final message was shown, False if the caller should send normally."""
    if not frames:
        return False
    msg = None
    try:
        if interaction is not None:
            for f in frames:
                await interaction.edit_original_response(content=f)
                await asyncio.sleep(ANIM_FRAME_DELAY)
            await interaction.edit_original_response(content=content, embed=embed)
            return True
        msg = await channel.send(content=frames[0])
        for f in frames[1:]:
            await asyncio.sleep(ANIM_FRAME_DELAY)
            await msg.edit(content=f)
        await asyncio.sleep(ANIM_FRAME_DELAY)
        await msg.edit(content=content, embed=embed)
        return True
    except Exception as e:
        print(f"[commentary] animation fell back ({type(e).__name__}: {e})")
        if msg is not None:
            try:
                await msg.edit(content=content, embed=embed)
                return True
            except Exception:
                pass
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Main entry
# ─────────────────────────────────────────────────────────────────────────────

def _dismissal_kind(shot_internal, delivery_internal, bowling_type) -> str:
    group = _DELIVERY_GROUP.get(delivery_internal, "")
    if group == "yorker":
        weights = {"bowled": 55, "lbw": 35, "caught": 10}
    elif group == "short":
        weights = {"caught": 90, "bowled": 10}
    else:
        weights = dict(_KIND_WEIGHTS_BY_SHOT.get(shot_internal, {"bowled": 40, "lbw": 30, "caught": 30}))
        if bowling_type not in _PACE_TYPES and shot_internal in ("Lofted", "Drive"):
            weights["stumped"] = 25
    kinds, w = zip(*weights.items())
    return random.choices(kinds, weights=w, k=1)[0]


def build_ball_commentary(
    bowler_name: str,
    bowler_ovr: int,
    delivery_button: str,
    delivery_internal: str,
    speed: float,
    batsman_name: str,
    shot_button: str,
    shot_internal: str,
    outcome: str,
    bowling_type: str = "Fast",
    stage1_choice: str = "",
    game=None,
    shot_quality: str | None = None,
) -> str:
    """One ball of commentary (Discord markdown, see module doc).

    game          optional: adds ball number, situation lines, no-repeat, animation.
                  Must be called BEFORE the ball is applied to the game (as views do).
    shot_quality  "good" (recommended shot), "bad" (guide existed, ignored), or None.
    """
    shot = bat_label(shot_button)
    ball = button_label(bowling_type, delivery_button) if delivery_button else "delivery"
    kw = dict(b=_short(batsman_name), bw=_short(bowler_name), shot=shot, ball=ball)
    tag = TAGS.get(outcome, str(outcome))
    kind = ""

    if outcome in EXTRA_LINES:
        main = _pick(game, EXTRA_LINES[outcome])
    elif outcome == "W":
        kind = _dismissal_kind(shot_internal, delivery_internal, bowling_type)
        main = _pick(game, WICKET_LINES[kind])
        flav = WICKET_FLAVOUR.get(_DELIVERY_GROUP.get(delivery_internal, ""))
        if flav and random.random() < 0.5:
            main = f"{main} {flav}"
    else:
        shot_pool = SHOT_LINES.get(shot_internal, {}).get(outcome)
        if shot_button == "Leave":
            shot_pool = SHOT_LINES["Leave"]["0"]
        if outcome == "0" and shot_button != "Leave" and shot_internal != "Defend":
            dpool = DOT_BY_DELIVERY.get(_DELIVERY_GROUP.get(delivery_internal, ""))
            if dpool and (not shot_pool or random.random() < 0.5):
                shot_pool = dpool
        main = _pick(game, shot_pool or GENERIC_LINES.get(outcome, ["No run."]))

    text = _esc(clean(_fill(main, **kw)))
    if kind:   # make the dismissal word stand out: **CAUGHT**
        word = _KIND_WORD[kind]
        text = text.replace(word, f"**{word}**", 1)
    body = [text]
    situation = _situation(
        game, outcome=outcome, batter=batsman_name, bowler=bowler_name,
        bowling_type=bowling_type, speed=speed, shot_quality=shot_quality, kind=kind,
    )
    if situation:
        body.append(SUBTEXT + _esc(clean(situation)))

    headline = _pick(game, HEADLINES[outcome]) if (outcome in HEADLINES and HEADLINE_BIG) else None
    header = _header(game, bowler_name, batsman_name, None if headline else tag)
    block = _render(header, body, headline)

    if game is not None:
        frames = _choose_animation(
            game, outcome, kind, _header(game, bowler_name, batsman_name, None), shot)
        if frames:
            game.__dict__["_anim_frames"] = frames
        else:
            game.__dict__.pop("_anim_frames", None)
    return block
