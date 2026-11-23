"""Extra commentary lines, added to the pools in commentary.py at import (more variety, fewer repeats).

Remove the import at the bottom of commentary.py to switch these off.
Rules (checked by _validate): only WICKET lines may contain stump / caught / catch / lbw / plumb /
in front, and every wicket line has exactly ONE dismissal word in capitals.
{b} batter  {bw} bowler  {shot} shot name  {ball} delivery name
"""

HEADLINES = {
    "6": ["Massive! **SIX**", "Gone, gone, gone! **SIX**", "Launched! **SIX**", "Sent into orbit! **SIX**",
          "Maximum! **SIX**", "Into the crowd! **SIX**",
          "Monster hit! **SIX**", "That's out of here! **SIX**"],
    "4": ["Beautiful! **FOUR**", "Bullet to the rope! **FOUR**", "Pierced the field! **FOUR**",
          "One bounce, over! **FOUR**", "Textbook! **FOUR**", "No chance for the fielder! **FOUR**"],
    "W": ["Huge wicket! **OUT**", "What a moment! **OUT**", "The crowd goes wild! **OUT**",
          "Breakthrough! **OUT**", "He's gone! **OUT**", "Big blow! **OUT**"],
}

SHOT_LINES = {
    "Drive": {
        "4": ["Glorious cover drive. The ball was only going one way.",
              "Leans forward, high elbow, and it races to the fence.",
              "Picture perfect {shot}. Hold that pose.",
              "Drilled past mid-off. The fielder does not even move.",
              "On the up and through extra cover like a rocket."],
        "6": ["Steps out and drives it straight back over {bw}'s head!",
              "Lofted drive, sails over long-off with ease.",
              "Clean strike down the ground. That landed in the crowd."],
        "2": ["Driven into the gap, quick legs, two runs.", "Pushed past cover, they turn for a second."],
        "1": ["Soft hands on the drive, one run.", "Driven to long-off, an easy single."],
        "0": ["Lovely drive, superb stop at cover.", "Driven firmly, straight to the fielder.",
              "Beaten on the drive, {ball} moves away late."],
    },
    "Lofted": {
        "6": ["Down on one knee and launched over mid-wicket!",
              "Pure muscle. That ball has gone a long way.",
              "Picks the length early and dispatches it into the stands.",
              "Swings through the line, and it is a monster hit.",
              "Over long-on and onto the roof!"],
        "4": ["Not timed fully, but it clears the infield and runs away for four.",
              "Over the bowler's head, bounces once and hits the rope."],
        "2": ["High in the air, falls safe. Two runs.", "Big swing, lands in no man's land, two."],
        "1": ["Mistimed lofted shot, they scamper a single.", "Heaved to deep mid-wicket, one."],
        "0": ["Big heave, beaten all ends up.", "Swings for the fences, connects with nothing.",
              "Tries to go big, {ball} too good."],
    },
    "Flick": {
        "4": ["Flicked off the pads, fine leg has no chance.", "Wristy flick, races through mid-wicket.",
              "Whipped off the hips, four runs."],
        "6": ["Flicked off the pads and over square leg! Pure wrists.",
              "Effortless flick, that has gone all the way."],
        "1": ["Tucked off the pads for one.", "Worked to square leg, single."],
        "0": ["Flick finds the fielder at mid-wicket.", "Tries to work it leg side, misses."],
    },
    "Pull": {
        "4": ["Pulled hard, ahead of square, four.", "Rocks back and pulls, races to the fence.",
              "Short ball punished. Four runs."],
        "6": ["Pulled with authority, over deep square leg!", "Hooked and gone. What a shot.",
              "Rocks back, swivels, and it lands in the crowd."],
        "1": ["Pulled down to deep square, one.", "Short ball, pulled for a single."],
        "0": ["Goes for the pull, too quick for him.", "Pulled straight to the fielder."],
    },
    "Cut": {
        "4": ["Cut hard, backward point dives in vain.", "Slashed past point, four.",
              "Width offered, and it is cut away for four."],
        "6": ["Upper cut over third man! Cheeky and brilliant.", "Cut over point, all the way!"],
        "1": ["Cut to deep point, one run.", "Dabbed down to third man, single."],
        "0": ["Cut, straight to point.", "Goes for the cut, beaten by the bounce."],
    },
    "Sweep": {
        "4": ["Swept fine, races to the fence.", "Gets down and sweeps, four runs.",
              "Slog sweep, beats deep square."],
        "6": ["Slog sweep, high and handsome!", "Down on one knee, swept over deep mid-wicket!"],
        "1": ["Swept to deep square, one.", "Paddled fine for a single."],
        "0": ["Sweep, misses, hit on the body.", "Swept straight to short fine."],
    },
    "Reverse-Sweep": {
        "4": ["Switches hands, reverse sweeps for four! Outrageous.", "Reverse sweep, beats third man."],
        "6": ["Reverse sweep over point for six! Unbelievable.", "Switch hit, that is gone!"],
        "1": ["Reverse swept to point, single.", "Cheeky reverse, one run."],
        "0": ["Tries the reverse, misses it.", "Reverse sweep straight to the fielder."],
    },
    "Defend": {
        "0": ["Solid forward defence.", "Plays it with a dead bat.", "Watchful, kept out.",
              "Bat and pad together, safe."],
        "1": ["Defended into the gap, quick single.", "Soft push, they steal one."],
    },
    "Leave": {
        "0": ["Shoulders arms. Good leave.", "Lets it go through to the keeper.", "Judges the line, no shot."],
    },
}

GENERIC_LINES = {
    "6": ["Huge hit. The crowd loves it.", "That is six more on the board.", "Clean strike, big six."],
    "4": ["Beautiful timing. Four.", "Into the gap and away. Four runs.", "Boundary. Well played."],
    "3": ["Great running, three runs.", "Pushing hard, they make it three."],
    "2": ["Quick running, two.", "Into the gap, back for the second."],
    "1": ["Single to keep the strike moving.", "Rotates the strike. One."],
    "0": ["Dot ball.", "Good ball, no run.", "Pressure builds. No run."],
}

DOT_BY_DELIVERY = {
    "yorker":   ["Toe-crushing {ball}. Somehow kept out.", "Dug out at the last moment."],
    "full":     ["Full and swinging, no room to hit.", "Driven, but no timing."],
    "short":    ["Bouncer, ducks under it.", "Short and sharp, he takes it on the body."],
    "swing":    ["Big swing, beats the bat.", "Late movement, he is lucky there."],
    "length":   ["Tight line, nothing to work with.", "Back of a length, defended."],
    "turn":     ["Rips past the bat.", "Grips and turns, beaten."],
    "wrong":    ["Read it late, just survives.", "Wrong one, beats the bat."],
    "straight": ["Skids through, kept out.", "Quicker one, blocked."],
}

WICKET_LINES = {
    "bowled":  ["BOWLED! Middle pole cartwheeling.", "BOWLED. Plays down the wrong line, timber!",
                "BOWLED him, the sound of the woodwork says it all.", "BOWLED through bat and pad!",
                "BOWLED! A perfect delivery, nothing he could do."],
    "lbw":     ["LBW! Dead straight, no doubt about it.", "Rapped on the pads, LBW given.",
                "LBW. Missed the line, pays the price.", "Struck low on the pad. LBW!"],
    "caught":  ["CAUGHT! Brilliant take in the deep.", "Thin edge, CAUGHT behind.",
                "CAUGHT at long-on, the fielder makes no mistake.", "Skied, and CAUGHT at mid-wicket.",
                "CAUGHT! Diving effort at point, what a grab."],
    "stumped": ["STUMPED! Lightning work behind the wickets.", "Down the track, misses, STUMPED.",
                "STUMPED. Keeper whips the bails off in a flash."],
}

EXTRA_LINES = {
    "Wd": ["WIDE. Way down the leg side.", "WIDE. Strays outside off.", "WIDE. Loses his radar."],
    "NB": ["NO BALL. Front foot over the line. Free hit!", "NO BALL. Overstepped, free hit coming."],
}

_BANNED = ("stump", "caught", "catch", "lbw", "plumb", "in front")
_WORDS = ("BOWLED", "LBW", "CAUGHT", "STUMPED")


def _validate() -> None:
    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                yield from walk(v)
        else:
            yield from x
    for pool in (HEADLINES, SHOT_LINES, GENERIC_LINES, DOT_BY_DELIVERY, EXTRA_LINES):
        for line in walk(pool):
            low = line.lower()
            assert not any(b in low for b in _BANNED), f"banned word in non-wicket line: {line}"
    for kind, lines in WICKET_LINES.items():
        for line in lines:
            assert sum(line.count(w) for w in _WORDS) == 1, f"wicket line needs one word: {line}"


def extend(mod) -> None:
    """Append these lines to commentary.py's pools (no duplicates)."""
    _validate()

    def add(dst: dict, src: dict):
        for k, v in src.items():
            if isinstance(v, dict):
                add(dst.setdefault(k, {}), v)
            else:
                lst = dst.setdefault(k, [])
                lst.extend(x for x in v if x not in lst)

    add(mod.HEADLINES, HEADLINES)
    add(mod.SHOT_LINES, SHOT_LINES)
    add(mod.GENERIC_LINES, GENERIC_LINES)
    add(mod.DOT_BY_DELIVERY, DOT_BY_DELIVERY)
    add(mod.WICKET_LINES, WICKET_LINES)
    add(mod.EXTRA_LINES, EXTRA_LINES)
