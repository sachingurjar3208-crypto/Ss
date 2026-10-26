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
ODDS = {"A": 3, "B": 10, "C": 87}

# ── Guarantee ────────────────────────────────────────────────────────────────
# The 50th draft since the user's last Pool A card is a 100% Pool A card.
# Any Pool A card (lucky 3% or guaranteed) restarts the count.
PITY_A_EVERY = 50

# Pool B guarantee: the 10th draft since the user's last Pool B card is a 100% Pool B card
# (so after 9 drafts without Pool B, the next one is guaranteed). Any Pool B card (lucky 10%
# or guaranteed) restarts the count. If Pool A is also due on the same draft, Pool A wins and
# the Pool B guarantee is used on the very next draft. 0 = no Pool B guarantee.
PITY_B_EVERY = 10

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
