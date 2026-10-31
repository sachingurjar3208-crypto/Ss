"""
DRS text frames (monospace, no emoji). Pure text, no Discord calls: views.DRSView shows them
one by one by editing the same message.

    drs_prompt(...)                 the "Review or walk off" box
    drs_frames(kind, result, ...)   the third-umpire animation; the LAST frame is the decision

kind   : "LBW" | "Caught" | "Bowled" | "Stumped"   (from views._dismissal_type)
result : "notout" | "out" | "ucall"                 ("ucall" = umpire's call, LBW only)
"""

# ── Settings ────────────────────────────────────────────────────────────────
DRS_FRAME_DELAY          = 1.3    # seconds between animation frames
DRS_UMPIRES_CALL_SHARE   = 0.30   # of upheld LBW reviews, this share is "umpire's call" (review kept)
DRS_BOWLED_NOBALL_FACTOR = 0.35   # bowled reviews: normal overturn chance x this = no ball found

_FENCE = "```"
_SEP = "-" * 36


def _block(text: str) -> str:
    return f"{_FENCE}\n{text}\n{_FENCE}"


def _bar(n: int) -> str:
    n = max(0, min(10, n))
    return "[" + "#" * n + "-" * (10 - n) + "]"


def _head(step: int, total: int) -> str:
    return f"THIRD UMPIRE REVIEW         {step}/{total}\n{_SEP}\n"


def _row(label: str, value: str) -> str:
    return f"{label:<16}{value}"


def _plain(name: str) -> str:
    """Surname only (like the commentary), max 12 chars so the box fits a phone."""
    try:
        from moments import short_name
        name = short_name(name) or name
    except Exception:
        pass
    return str(name or "").replace("`", "'")[:12]


def drs_prompt(kind: str, batter: str, runs: int, balls: int, bowler: str,
               reviews: int, seconds: int = 10) -> str:
    return _block("\n".join([
        "DECISION REVIEW SYSTEM",
        _SEP,
        _row("On-field call", f"OUT ({kind.upper()})"),
        _row("Batter", f"{_plain(batter)}  {runs} ({balls})"),
        _row("Bowler", _plain(bowler)),
        _row("Reviews left", str(reviews)),
        _SEP,
        f"Review it or walk off.  {seconds}s",
    ]))


def _verdict(result: str, reviews_after: int, *, notout_reason: str = "Overturned.",
             kept_reason: str = "Umpire's call.") -> str:
    if result == "notout":
        return f"{_SEP}\n{_row('DECISION', 'NOT OUT')}\n{notout_reason} Review kept, {reviews_after} left."
    if result == "ucall":
        return f"{_SEP}\n{_row('DECISION', 'OUT')}\n{kept_reason} Review kept, {reviews_after} left."
    return f"{_SEP}\n{_row('DECISION', 'OUT')}\nUpheld. Review lost, {reviews_after} left."


def drs_frames(kind: str, result: str, reviews_after: int) -> list[str]:
    """Animation frames for one review. The last frame shows the final decision."""
    ff = "Front foot"
    if kind == "LBW":
        impact = "OUTSIDE OFF" if result == "notout" else "IN LINE"
        wickets = "UMPIRE'S CALL" if result == "ucall" else "HITTING"
        frames = [
            _head(1, 4) + _row(ff, "checking..."),
            _head(2, 4) + _row(ff, "FAIR") + "\n" + _row("Ball tracking", _bar(4)),
            _head(3, 4) + "\n".join([_row(ff, "FAIR"), _row("Pitching", "IN LINE"),
                                     _row("Impact", impact), _row("Wickets", _bar(7))]),
            _head(4, 4) + "\n".join([_row(ff, "FAIR"), _row("Pitching", "IN LINE"),
                                     _row("Impact", impact), _row("Wickets", wickets)])
            + "\n" + _verdict(result, reviews_after),
        ]
    elif kind == "Caught":
        edge = result != "notout"
        wave = "_____/\\/\\____" if edge else "______________"
        frames = [
            _head(1, 4) + _row(ff, "checking..."),
            _head(2, 4) + _row(ff, "FAIR") + "\n" + _row("UltraEdge", "loading..."),
            _head(3, 4) + _row(ff, "FAIR") + "\nUltraEdge\n  bat  ......|......\n  wave " + wave,
            _head(4, 4) + "\n".join([_row(ff, "FAIR"),
                                     _row("UltraEdge", "SPIKE AT BAT" if edge else "NO SPIKE"),
                                     _row("Carry", "CLEAN" if edge else "-")])
            + "\n" + _verdict("out" if edge else "notout", reviews_after,
                              notout_reason="Off the pad only."),
        ]
    elif kind == "Bowled":
        noball = result == "notout"
        frames = [
            _head(1, 3) + _row(ff, "checking..."),
            _head(2, 3) + "Front foot frame by frame\n  line  |\n  heel  " + ("   [==]" if noball else "[==]   "),
            _head(3, 3) + _row(ff, "NO BALL" if noball else "BEHIND THE LINE") + "\n"
            + (f"{_SEP}\n{_row('DECISION', 'NOT OUT')}\nNo ball. FREE HIT next ball.\nReview kept, {reviews_after} left."
               if noball else _verdict("out", reviews_after)),
        ]
    else:  # Stumped
        grounded = result == "notout"
        frames = [
            _head(1, 3) + _row("Bails off", "frame 1"),
            _head(2, 3) + _row("Bails off", "frame 3") + "\n" + _row("Back foot", _bar(6)),
            _head(3, 3) + _row("Back foot", "GROUNDED" if grounded else "IN THE AIR") + "\n"
            + _verdict("notout" if grounded else "out", reviews_after, notout_reason="Just in time."),
        ]
    return [_block(f) for f in frames]
