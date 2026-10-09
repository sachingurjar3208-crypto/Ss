"""Central place for the bot's custom (Developer Portal / application) emojis.

Every emoji ID lives HERE. If you re-upload an emoji and its ID changes, edit
the one line below and the whole bot updates.

Custom emojis only render in message text, embed titles / descriptions /
field names + values, and buttons or select options (via emoji=...). They do
NOT render in embed footers, embed author names, code blocks (`...` / ```...```)
or inside images, so those spots keep plain text.

RULE: WON / LOSS (the W and L emojis) are for the profile form only
(csprofile). Do not use them anywhere else.
"""

# ── Emoji IDs (copied from the Developer Portal → Emojis) ────────────────────
LOSS_PROFILE = "<:Lossprofile:1558139747275243643>"   # profile form only
WON_PROFILE  = "<:Wonprofile:1558139744008020088>"    # profile form only
DAILY        = "<:Dailypack:1558139740786528368>"
STREAK       = "<:Streak:1558139736617390292>"
SQUAD_GROUP  = "<:Squademoji:1558139700156571801>"    # group icon: teams / XI
CAPTAIN      = "<:Captain:1558139695177932951>"
OVR          = "<:OVR:1558139691482751066>"
SQUAD        = "<:Squad:1558139688231903322>"         # single player icon: squad / players
COIN         = "<:CSCoin:1558139683702055062>"
MONTHLY      = "<:Monthlypack:1558138440208486440>"
WEEKLY       = "<:Weeklypack:1558138384675905536>"

# Reward emoji by claim type (csdaily / csweekly / csmonthly)
REWARD = {"daily": DAILY, "weekly": WEEKLY, "monthly": MONTHLY}


# ── Small helpers so the wording stays consistent everywhere ─────────────────

def coins(n) -> str:
    """12,500 <coin>"""
    return f"{int(n):,} {COIN}"


def ovr(n) -> str:
    """<OVR badge> 85  (the badge already says OVR)"""
    return f"{OVR} **{n}**"


def reward_label(kind: str) -> str:
    """<pack emoji> **Daily**"""
    return f"{REWARD.get(kind, '🎁')} **{kind.title()}**"
