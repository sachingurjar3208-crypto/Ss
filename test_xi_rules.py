"""Quick checks for the new Playing XI role-composition rules."""
import card_db
import economy
import squad_logic as sl

card_db.init_card_db()
economy.init_economy_db()

UID = 123123123

PLAYERS = [
    # 5 BAT, 1 WK, 2 AR, 3 BOWL = 11, satisfies all min/max
    ("X Opener A", "BAT", 88, 20, None, "India"),
    ("X Opener B", "BAT", 85, 15, None, "India"),
    ("X No3", "BAT", 90, 10, None, "India"),
    ("X No4", "BAT", 86, 10, None, "India"),
    ("X No5", "BAT", 84, 10, None, "India"),
    ("X Keeper", "WK", 80, 10, None, "India"),
    ("X AR A", "AR", 75, 72, "Off Break", "India"),
    ("X AR B", "AR", 70, 75, "Leg Break", "India"),
    ("X Bowl A", "BOWL", 25, 85, "Fast", "India"),
    ("X Bowl B", "BOWL", 20, 82, "Swing", "India"),
    ("X Bowl C", "BOWL", 15, 80, "Off Break", "India"),
]

for name, role, bat, bowl, bt, country in PLAYERS:
    if not card_db.card_exists(name):
        card_db.create_card(
            playername=name, displayname=name.split()[-1],
            foreground_link="https://example.com/x.png", background="default",
            ovr=round((bat + bowl) / 2) + 5, bat=bat, bowl=bowl,
            country=country, country_emoji="🏳️", created_by=UID,
            role=role, batting_hand="R", bowling_type=bt,
        )

economy.create_user(UID, "Rule Test XI")
for name, *_ in PLAYERS:
    economy.give_card(UID, name.strip().lower())

print("Compliant XI (5 BAT/1 WK/2 AR/3 BOWL):")
print("  xi_problem:", sl.xi_problem(UID) or "None -> OK, match can start")
print("  counts:", sl.xi_role_counts(sl.xi_cards(UID)))

print("\nbest_xi() auto-pick test (pool of 14 players, lots of extra BOWL):")
pool = sl.owned_cards(UID) + [
    card_db.get_card(k) for k in (
        "x extrabowl a", "x extrabowl b", "x extrabowl c",
    )
] if False else None  # placeholder, real extra-pool test below

# Build a bigger pool: compliant 11 + 5 extra bowlers, to see if best_xi()
# still respects the BOWL max of 5 instead of stuffing the XI with bowlers.
EXTRA = [
    ("X Bowl D", "BOWL", 10, 90, "Yorker", "India"),
    ("X Bowl E", "BOWL", 10, 88, "Bouncer", "India"),
    ("X Bowl F", "BOWL", 10, 86, "Googly", "India"),
]
for name, role, bat, bowl, bt, country in EXTRA:
    if not card_db.card_exists(name):
        card_db.create_card(
            playername=name, displayname=name.split()[-1],
            foreground_link="https://example.com/x.png", background="default",
            ovr=round((bat + bowl) / 2) + 5, bat=bat, bowl=bowl,
            country=country, country_emoji="🏳️", created_by=UID,
            role=role, batting_hand="R", bowling_type=bt,
        )
    economy.give_card(UID, name.strip().lower())

full_pool = sl.owned_cards(UID)
print(f"  pool size: {len(full_pool)}")
picked = sl.best_xi(full_pool)
print(f"  best_xi picked: {len(picked)} players")
print("  counts:", sl.xi_role_counts(picked))
