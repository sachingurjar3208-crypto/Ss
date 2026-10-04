"""One-off script: adds 11 test players to the card database, creates a test
user, gives them all 11 cards (auto-fills the XI), and prints the XI status.

Run with:  python3 seed_test_players.py
"""
import card_db
import economy
import squad_logic as sl

card_db.init_card_db()
economy.init_economy_db()

TEST_USER_ID = 999999999  # fake discord id for testing

# name, role, bat, bowl, bowling_type, country
TEST_PLAYERS = [
    ("Test Opener A",    "BAT",  88, 20, None,          "India"),
    ("Test Opener B",    "BAT",  85, 15, None,          "Australia"),
    ("Test No3",         "BAT",  90, 10, None,          "England"),
    ("Test Keeper",      "WK",   80, 10, None,          "South Africa"),
    ("Test AllRounder A","AR",   75, 72, "Off Break",   "India"),
    ("Test AllRounder B","AR",   70, 75, "Leg Break",   "Pakistan"),
    ("Test FastBowler A","BOWL", 25, 85, "Fast",        "Australia"),
    ("Test FastBowler B","BOWL", 20, 82, "Swing",       "England"),
    ("Test SpinBowler A","BOWL", 15, 80, "Off Break",   "India"),
    ("Test SpinBowler B","BOWL", 15, 78, "Leg Break",   "Sri Lanka"),
    ("Test DeathBowler", "BOWL", 20, 83, "Yorker",      "New Zealand"),
]

print("Creating cards...")
for name, role, bat, bowl, bowling_type, country in TEST_PLAYERS:
    if card_db.card_exists(name):
        print(f"  (already exists) {name}")
        continue
    card_db.create_card(
        playername=name,
        displayname=name.split()[-1],
        foreground_link="https://example.com/placeholder.png",
        background="default",
        ovr=round((bat + bowl) / 2) + 5,
        bat=bat,
        bowl=bowl,
        country=country,
        country_emoji="🏳️",
        created_by=TEST_USER_ID,
        role=role,
        batting_hand="R",
        bowling_type=bowling_type,
    )
    print(f"  created {name} ({role})")

print("\nSetting up test user & giving cards...")
economy.create_user(TEST_USER_ID, "Test XI")
for name, *_ in TEST_PLAYERS:
    key = name.strip().lower()
    ok = economy.give_card(TEST_USER_ID, key)
    print(f"  give_card {name}: {'ok' if ok else 'already owned / failed'}")

print("\nXI status:")
problem = sl.xi_problem(TEST_USER_ID)
print("  xi_problem:", problem or "None -> XI is valid!")

cards = sl.xi_cards(TEST_USER_ID)
print(f"  players in XI: {len(cards)}/{economy.XI_SIZE}")
bowlers = sum(1 for c in cards if card_db.effective_bowling_type(c) is not None)
print(f"  bowler-capable: {bowlers} (need >= {sl.MIN_BOWLERS})")
for c in cards:
    print(f"   - {c['playername']:20s} role={card_db.effective_role(c):4s} "
          f"bat={c['bat']:3d} bowl={c['bowl']:3d} "
          f"bowling_type={card_db.effective_bowling_type(c)}")
