"""Draft (voucher) system settings. EVERYTHING you may want to change is here.

Pools
  Pool A  fixed cards  -> added with `/pool add` (each with its own chance)
  Pool B  fixed cards  -> added with `/pool add`
  Pool C  random card between POOL_C_OVR_MIN and POOL_C_OVR_MAX (all cards in that range)

"""

# ── Fixed cards (Pool A / B) ─────────────────────────────────────────────────
# Not written here any more: add / remove them in Discord with
#   /pool add     /pool remove     /pool list
# Each card has its own chance inside its pool.

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
