"""Users who get every bot Premium perk WITHOUT the Premium role (e.g. a bot-list reviewer testing
the bot in their own server, where the role can't exist). Add or remove IDs here, then restart."""
PREMIUM_USER_IDS = {
    333949691962195969,   # Top.gg Verification Center (bot review)
}


def whitelisted(user_id) -> bool:
    try:
        return int(user_id) in PREMIUM_USER_IDS
    except (TypeError, ValueError):
        return False
