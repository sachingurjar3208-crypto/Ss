import random
from data import SHOT_DESCRIPTIONS, get_delivery_description, DELIVERY_BUTTON_MAP

# Short flavor lines (≤8 words) so the whole commentary block stays tiny.
WICKET_TEXTS = {
    "Fast":         ["Nips off the seam — stumps shattered!", "Too quick! Thunderbolt of a delivery!"],
    "Swing":        ["Swings late and clips off stump!", "Swings back in — plumb in front!"],
    "Yorker":       ["PERFECT YORKER! Uproots the stumps!", "Unplayable! Crashes into middle stump!"],
    "Bouncer":      ["Fended straight to leg gully!", "Top-edged the pull — taken!"],
    "Good Length":  ["Nips back — traps him plumb LBW!", "Late movement — caught at slip!"],
    "Full":         ["Wicket-to-wicket — LBW, no hesitation!", "Driven straight back — caught and bowled!"],
    "Leg Break":    ["Spins sharply — sends stumps flying!", "Ripped through the gate — no clue!"],
    "Googly":       ["Deceived by the googly — bowled!", "Beats the outside edge — gone!"],
    "Flipper":      ["Skids on low — trapped LBW!", "Low and fast — crashes into stumps!"],
    "Off Break":    ["Turns sharply, clips off stump!", "Through the gate — bowled him!"],
    "Doosra":       ["Went the other way — no clue!", "Doosra magic — top of off!"],
    "Carrom Ball":  ["Turns sharply — edge to slip!", "Wrong way off the fingers — bowled!"],
    "Arm Ball":     ["Doesn't turn — crashes into stumps! LBW!", "Straight on — caught at short leg!"],
    "Top Spin":     ["Extra bounce — gloves it through!", "Dips late — miscued to mid-on!"],
    "Drift Ball":   ["Drifted in, spun away — plumb!", "Big drift then spin — edged!"],
    "Top Spinner":  ["Skids through low — struck in front!", "Extra pace — back into stumps!"],
    "Slider":       ["Slides through — clips off stump!", "Deceived in flight — job done!"],
}

GENERIC_WICKET = [
    "He's gone! Beats the bat completely!",
    "OUT! The bowler is pumped!",
    "What a catch! He has to go!",
    "BOWLED! Stumps all over the place!",
    "LBW! Plumb — no hesitation there!",
]

DOT_TEXTS = [
    "Dot ball — excellent discipline!",
    "Beaten outside off stump!",
    "Good length, defended solidly.",
    "Tight line — nothing to hit.",
    "Pushed to mid-on — no run.",
    "Left alone — good leave!",
    "Defended with soft hands.",
    "Kept out — pressure building!",
]

FOUR_TEXTS = [
    "Cracked through the covers — FOUR!",
    "Driven hard and true — FOUR!",
    "Perfectly placed through mid-wicket!",
    "Too short, too wide — punished!",
    "Races away to the ropes!",
    "Cut hard and square — FOUR!",
    "Flicked off the hips — FOUR!",
]

SIX_DISTANCES = list(range(72, 109))

DIGIT_EMOJIS = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]

def _num_to_emoji(n: int) -> str:
    return "".join(DIGIT_EMOJIS[int(d)] for d in str(n))


MAX_COMMENTARY_WORDS = 18


def _limit_words(text: str, max_words: int) -> str:
    """Hard cap: the whole commentary block never exceeds `max_words` words.
    Drops whole lines from the end first, then trims mid-line if one line
    alone is still too long."""
    lines = text.split("\n")
    kept, total = [], 0
    for line in lines:
        words = line.split()
        if total >= max_words:
            break
        room = max_words - total
        if len(words) > room:
            words = words[:room]
        kept.append(" ".join(words))
        total += len(words)
    return "\n".join(kept)


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
) -> str:
    """Short result commentary (max MAX_COMMENTARY_WORDS words). The bowler,
    delivery type and speed are already announced in the "is coming with ...
    kmph" message before the shot is played, so this only describes the shot
    and, for a boundary/wicket, one short flavour line."""
    delivery_desc = get_delivery_description(delivery_button, delivery_internal)
    shot_verb = SHOT_DESCRIPTIONS.get(shot_internal, shot_button.lower() + "s")

    lines = [f"**{batsman_name}** {shot_verb} the {delivery_desc}"]

    if outcome == "6":
        dist = random.choice(SIX_DISTANCES)
        lines.append(f"**{_num_to_emoji(dist)}m — THAT'S A SIX!**")
    elif outcome == "4":
        lines.append(f"**{random.choice(FOUR_TEXTS)}**")
    elif outcome == "W":
        wkt_pool = WICKET_TEXTS.get(delivery_internal, GENERIC_WICKET)
        lines = [f"**{random.choice(wkt_pool)}** {batsman_name} is OUT ☝️"]
    elif outcome == "Wd":
        lines = ["**Wide!** Extra run awarded."]
    elif outcome == "NB":
        lines = ["**No Ball!** Free hit next ball! 🆓"]
    elif outcome == "NB+1":
        lines = ["**No Ball!** Plus a run — two extras! 🆓"]
    elif outcome == "0":
        lines.append(f"*{random.choice(DOT_TEXTS)}*")

    return _limit_words("\n".join(lines), MAX_COMMENTARY_WORDS)
