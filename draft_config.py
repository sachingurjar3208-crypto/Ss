"""Draft (voucher) system settings. EVERYTHING you may want to change is here.

Pools
  Pool A  fixed cards  -> write the player names in POOL_A_CARDS
  Pool B  fixed cards  -> write the player names in POOL_B_CARDS
  Pool C  random card between POOL_C_OVR_MIN and POOL_C_OVR_MAX (all cards in that range)

Names are matched like `csgivecard` does (type enough of the name to be unique,
or the exact card name). A name that can't be found is skipped and printed in
the bot console at start-up, so a typo never crashes the draft.
"""

# ── Fixed cards ──────────────────────────────────────────────────────────────
POOL_A_CARDS: list[str] = [
    # "Virat Kohli",
    # "MS Dhoni",
]
POOL_B_CARDS: list[str] = [
    # "Rohit Sharma",
]

# ── Pool C: random card from this OVR range ──────────────────────────────────
POOL_C_OVR_MIN = 60
POOL_C_OVR_MAX = 79

# ── Odds (per draft, in %). Must add up to 100. ──────────────────────────────
ODDS = {"A": 3, "B": 22, "C": 75}

# ── Guarantee ────────────────────────────────────────────────────────────────
# The 50th draft since the user's last Pool A card is a 100% Pool A card.
# Any Pool A card (lucky 3% or guaranteed) restarts the count.
PITY_A_EVERY = 50

# ── Cost ─────────────────────────────────────────────────────────────────────
VOUCHERS_PER_DRAFT = 1
DRAFT_MULTI = 10            # the "10 Drafts" button

# ── Pool A animation (you add the file later) ────────────────────────────────
# Put your animation (GIF) in the bot folder with this name. Until the file
# exists nothing is shown and Pool A cards reveal like normal ones.
POOL_A_ANIMATION_FILE = "pool_a_animation.gif"
POOL_A_ANIMATION_SECONDS = 6      # how long the animation stays before the result appears

# ── Admin limits for `voucher give / remove` ─────────────────────────────────
VOUCHER_ADMIN_MAX = 10_000        # most vouchers one command can give/remove
