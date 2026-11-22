"""Central place for the bot's custom (Developer Portal / application) emojis.

Every emoji ID lives HERE. If you re-upload an emoji and its ID changes, edit
the one line below and the whole bot updates.

Custom emojis only render in message text, embed titles / descriptions /
field names + values, and buttons or select options (via emoji=...). They do
NOT render in embed footers, embed author names, code blocks (`...` / ```...```)
or inside images, so those spots keep plain text.

RULE: WON / LOSS (the W and L emojis) are for the profile form only
(rcprofile). Do not use them anywhere else.
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
COIN         = "<:RCOIN:1571431001006284816>"            # RC Coin (was CSCoin)
RCP          = "<:RCP:1571429731264368692>"              # RCP points (premium currency)
PREMIUM      = "<:rcpremium:1571468249638637678>"
STAR_PASS    = "<:rcstarpass:1571468256101797949>"
TICK         = "<:rctick:1571468261751787632>"        # store: you can buy it
LOCK         = "<:rclock:1571468242218655754>"        # store: you need more RCP
ARROW        = "<:rcarrow:1571468224216961164>"
GIFT         = "<:rcgift:1571468238116618331>"        # Premium monthly RCP bonus
TICKET       = "<:rcticket:1571468266893873222>"      # "open a ticket"
CART         = "<:rccart:1571468229224697938>"        # purchases
MONTHLY      = "<:Monthlypack:1558138440208486440>"
WEEKLY       = "<:Weeklypack:1558138384675905536>"
VOUCHER      = "<:Voucher:1558696772975464458>"       # Draft currency

# Pack emojis (Developer Portal -> Emojis). Used in rcpack, rcopen, rcinv etc.
BRONZE_PACK    = "<:Bronzepack:1564835637490884659>"
SILVER_PACK    = "<:Silverpack:1564835559841599560>"
GOLD_PACK      = "<:Goldpack:1564835565323423764>"
LEGENDARY_PACK = "<:Legpack:1564835557396189345>"
HALLOWEEN_PACK = "<:Halloweenpack:1564836597046837258>"
RETRO_PACK = "<:Retropack:1570481667108503642>"
EOAE_PACK = "🏆"   # replace with the custom End of an Era pack emoji once it is uploaded

# Player roles (Developer Portal names)
WICKETKEEPER = "<:Wicketkepper:1558757467045888100>"   # portal name is spelled "Wicketkepper"
OFFSPIN      = "<:Offspin:1558778042925449297>"
LEGSPIN      = "<:Legspin:1558757461081722882>"
ALLROUNDER   = "<:Allrounder:1558757458762014730>"
BATTER       = "<:Batter:1558757456337965186>"
PACER        = "<:Pacer:1558757453846548560>"
MEDIUMPACER  = "<:Mediumpacer:1558757449878605884>"

# Card marker shown first in every XI row for cards that are NOT part of an event.
# (Event cards show their own event emoji instead, set with /eventmaker.)
# If this emoji is animated, write it as <a:Name:ID> instead.
DEFAULT_CARD_EMOJI = "<:Card:1562083735879295057>"

# Milestone / wicket emojis (animated: <a:...>)
HALFCENTURY  = "<a:Halfcentury:1558777604042137700>"
CENTURY      = "<a:Century:1558781921389977650>"
LBW          = "<a:LBW:1558777617434419240>"

# Reward emoji by claim type (rcdaily / rcweekly / rcmonthly)
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
