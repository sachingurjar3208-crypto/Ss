#!/usr/bin/env python3
"""Self-test for the two-step direction batting mapping (data.translate_shot).

Covers every height x direction x {a pace delivery, a spin delivery}
combination and checks:
  - the result is always one of BATTING_BUTTONS
  - direction "C" (Defend) always returns "Defend", for every height
  - height == "Loft" always returns "Loft", for every direction
  - the pace/spin split on Square Leg (R) and Mid-wicket (DR) is correct
"""
import sys

from data import DIRECTIONS, BATTING_BUTTONS, SPIN_DELIVERIES, translate_shot

PACE_DELIVERY = "Yorker"     # not in SPIN_DELIVERIES
SPIN_DELIVERY = "Off Break"  # in SPIN_DELIVERIES

assert PACE_DELIVERY not in SPIN_DELIVERIES
assert SPIN_DELIVERY in SPIN_DELIVERIES

failures = []


def check(cond: bool, msg: str):
    if not cond:
        failures.append(msg)


for height in ("Loft", "Ground"):
    for delivery in (PACE_DELIVERY, SPIN_DELIVERY):
        for key in DIRECTIONS:
            result = translate_shot(height, key, delivery)
            check(
                result in BATTING_BUTTONS,
                f"translate_shot({height!r}, {key!r}, {delivery!r}) -> {result!r} "
                f"is not in BATTING_BUTTONS",
            )

# Rule 1: Defend ignores height.
for height in ("Loft", "Ground"):
    for delivery in (PACE_DELIVERY, SPIN_DELIVERY):
        result = translate_shot(height, "C", delivery)
        check(result == "Defend", f"Defend rule broken for height={height}, delivery={delivery}: got {result!r}")

# Rule 2: Loft always maps to "Loft", for every direction EXCEPT the centre
# (direction "C" / Defend overrides height per rule 1, checked above).
for key in DIRECTIONS:
    if key == "C":
        continue
    for delivery in (PACE_DELIVERY, SPIN_DELIVERY):
        result = translate_shot("Loft", key, delivery)
        check(result == "Loft", f"Loft rule broken for direction={key}, delivery={delivery}: got {result!r}")

# Rule 3: Ground mapping, non pace/spin-dependent directions.
GROUND_FIXED = {
    "D":  "Drive",
    "DL": "Drive",
    "L":  "Cut",
    "UL": "Cut",
    "U":  "Scoop",
    "UR": "Flick",
}
for key, expected in GROUND_FIXED.items():
    for delivery in (PACE_DELIVERY, SPIN_DELIVERY):
        result = translate_shot("Ground", key, delivery)
        check(result == expected, f"Ground/{key} rule broken for delivery={delivery}: expected {expected!r}, got {result!r}")

# Rule 3 (pace/spin split): Square Leg (R) and Mid-wicket (DR).
check(translate_shot("Ground", "R", PACE_DELIVERY) == "Pull",
      "Square Leg vs pace should be Pull")
check(translate_shot("Ground", "R", SPIN_DELIVERY) == "Sweep",
      "Square Leg vs spin should be Sweep")
check(translate_shot("Ground", "DR", PACE_DELIVERY) == "Flick",
      "Mid-wicket vs pace should be Flick")
check(translate_shot("Ground", "DR", SPIN_DELIVERY) == "Sweep",
      "Mid-wicket vs spin should be Sweep")

# Sanity from the spec: Yorker guide ["Flick", "Defend"] = Fine Leg /
# Mid-wicket(pace) / centre Defend.
check(translate_shot("Ground", "UR", "Yorker") == "Flick", "Fine Leg vs Yorker should be Flick")
check(translate_shot("Ground", "DR", "Yorker") == "Flick", "Mid-wicket vs Yorker (pace) should be Flick")
check(translate_shot("Ground", "C", "Yorker") == "Defend", "Centre vs Yorker should be Defend")

# Fast Bouncer guide ["Cut"] = Point / Third Man on the ground.
check(translate_shot("Ground", "L", "Bouncer") == "Cut", "Point vs Bouncer should be Cut")
check(translate_shot("Ground", "UL", "Bouncer") == "Cut", "Third Man vs Bouncer should be Cut")

if failures:
    print(f"FAILED ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

total_checked = 2 * 2 * len(DIRECTIONS)
print(f"OK — {total_checked} height x delivery x direction combinations checked, all rules hold.")
sys.exit(0)
