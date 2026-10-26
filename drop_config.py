"""All tunable values for the drop system. Change numbers here, not in the cog."""
from bot_guard import ADMIN_GUILD_ID

GUILD_ID = ADMIN_GUILD_ID                  # the main server
DROP_CHANNEL_IDS = (1556662732017766450, 1556644440280535243)

# Spawn logic
SPAWN_MIN = 15                             # random target per cycle: 15..20 messages
SPAWN_MAX = 20
WAIT_AFTER_TARGET = 10 * 60                # seconds to wait after the target is reached
ARM_DELAY = 6                              # seconds between the arming message and the drop
INACTIVITY_LIMIT = 60 * 60                 # no human message for this long -> armed
STATE_TICK_SECONDS = 15                    # how often the timers are checked

# Drop
DROP_EXPIRE_SECONDS = 60

# Outcome weights (sum = 100)
OUTCOME_WEIGHTS = {
    "card": 18.5,
    "voucher": 20,
    "gold": 10,
    "silver": 20,
    "bronze": 30,       # quantity below
    "legendary": 0.5,
    "weekly": 1,
}
PACK_QTY = {"bronze": 3}                   # every other pack = 1

# Card OVR weights (relative, no need to sum to 100)
CARD_OVR_WEIGHTS = {
    92: 0.5, 91: 0.8, 90: 0.9, 89: 1, 88: 2, 87: 3, 86: 4, 85: 5,
    84: 6, 83: 7, 82: 8, 81: 9, 80: 10, 79: 20, 78: 21, 77: 22, 76: 23,
    **{ovr: 1 for ovr in range(60, 76)},   # 60-75: placeholder, equal weight
}
