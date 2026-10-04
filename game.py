from __future__ import annotations

from __future__ import absolute_import
import asyncio
import logging
import math
import random
from typing import TYPE_CHECKING, Any, Optional

import discord

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bd_models.models import BallInstance


def _pname(player) -> str:
    """Return a player's display name for both legacy dicts and MatchPlayer."""
    if player is None:
        return ""
    if isinstance(player, dict):
        return str(player.get("name", ""))
    return str(getattr(player, "name", player))


class MatchPlayer:
    """Lightweight representation of one card/player in a live match."""

    __slots__ = (
        "name",
        "ovr",
        "bat",
        "bowl",
        "bowling_type",
        "role",
        "inst_pk",
        "card_image_path",
        "bat_style",
        "rarity",
        "memories_type",
        "fielder_grade",
        "narrative_attrs",
    )

    def __init__(
        self,
        name: str,
        ovr: int,
        bat: int,
        bowl: int,
        bowling_type: str | None,
        role: str = "Bat",
        inst_pk: int = 0,
        card_image_path: str = "",
        bat_style: str = "balanced",
        rarity: float = 1.0,
        memories_type: str | None = None,
        fielder_grade: int = 1,
        narrative_attrs: list[str] | None = None,
    ):
        self.name = name
        self.ovr = ovr
        self.bat = bat
        self.bowl = bowl
        self.bowling_type = bowling_type
        self.role = role
        self.inst_pk = inst_pk
        self.card_image_path = card_image_path
        self.bat_style = bat_style
        self.rarity = rarity
        self.memories_type = memories_type
        self.fielder_grade = fielder_grade
        self.narrative_attrs = narrative_attrs or []

    def can_bowl(self) -> bool:
        if self.role and self.role.upper() == "WK":
            return False
        r = (self.role or "").upper()
        return (
            self.bowling_type is not None
            or self.bowl >= 60
            or r in ("AR", "BOWL", "BAT")
        )

    def effective_bowling_type(self) -> str:
        return self.bowling_type or "Fast"

    def __repr__(self) -> str:
        return f"<MatchPlayer {self.name!r} ovr={self.ovr} role={self.role}>"


class Partnership:
    """Records one batting partnership."""

    __slots__ = (
        "batsman1",
        "batsman1_pk",
        "batsman2",
        "batsman2_pk",
        "runs",
        "balls",
        "start_over",
        "dismissal",
    )

    def __init__(
        self,
        batsman1: str,
        batsman2: str,
        start_over: float,
        batsman1_pk: int = 0,
        batsman2_pk: int = 0,
    ):
        self.batsman1 = batsman1
        self.batsman1_pk = batsman1_pk
        self.batsman2 = batsman2
        self.batsman2_pk = batsman2_pk
        self.runs = 0
        self.balls = 0
        self.start_over = start_over
        self.dismissal: str | None = None

    def add_runs(self, r: int) -> None:
        self.runs += r

    def add_ball(self) -> None:
        self.balls += 1

    def shorthand(self, name: str | None = None) -> str:
        """Batsman name shorthand for display."""
        if not name:
            return "Unknown"
        if name.startswith("name:"):
            return name[5:13]
        return name[:13]


class BallDirection:
    """Captures the direction a boundary was hit for the wagon wheel."""

    __slots__ = ("over", "ball", "batsman", "bowler", "runs", "direction", "shot")

    def __init__(
        self,
        over: int,
        ball: int,
        batsman: str,
        bowler: str,
        runs: int,
        direction: str,
        shot: str,
    ):
        self.over = over
        self.ball = ball
        self.batsman = batsman
        self.bowler = bowler
        self.runs = runs
        self.direction = direction  # one of 12 clock positions e.g. "3 o'clock"
        self.shot = shot


class GameState:
    def __init__(
        self,
        channel_id: int,
        challenger=None,
        opponent=None,
        overs: int = 10,
        challenger_xi=None,
        opponent_xi=None,
    ):
        # The deployed match flow historically used
        # ``GameState(challenger, opponent, overs)``.  Keep that API while
        # accepting the newer channel-aware form as well.
        if not isinstance(channel_id, int):
            legacy_challenger, legacy_opponent, legacy_overs = (
                channel_id,
                challenger,
                opponent,
            )
            channel_id = getattr(legacy_challenger, "channel_id", 0)
            challenger, opponent = legacy_challenger, legacy_opponent
            if legacy_overs is not None:
                overs = legacy_overs
        self.channel_id = channel_id
        self.challenger = challenger
        self.opponent = opponent
        self.overs = overs
        self.bot = None
        self.is_bot_match: bool = False
        self.teams: dict[int, list[MatchPlayer]] = {}
        self.team_pks: dict[int, list[int]] = {}

        # Toss / Order
        self.toss_winner_id: int | None = None
        self.toss_note: str | None = None   # e.g. "Mumbai XI chose to bat first"
        self.batting_user_id: int | None = None
        self.bowling_user_id: int | None = None
        self.innings1_batting_user_id: int | None = None

        # On-field players
        self.striker: MatchPlayer | None = None
        self.non_striker: MatchPlayer | None = None
        self.current_bowler: MatchPlayer | None = None

        # Innings counters [inn1, inn2]
        self.innings: int = 1
        self.runs: list[int] = [0, 0]
        self.wickets: list[int] = [0, 0]
        self.legal_balls: list[int] = [0, 0]

        # Stats
        self.batsman_stats: dict[str, dict] = {}
        self.bowler_stats: dict[str, dict] = {}
        self.dismissed: list[str] = []
        self.bowler_ball_count: dict[str, int] = {}
        self.bowler_maidens: dict[str, int] = {}
        self.last_over_bowler: str | None = None

        # Cached card stats (inst_pk -> CricketerCardStats)
        self._card_stats_cache: dict[int, Any] = {}

        # Partnership tracking (current + history)
        self.current_partnership: Partnership | None = None
        self.partnership_history: list[Partnership] = []

        # Per-over history for analytics
        self.over_history: list[dict] = (
            []
        )  # list of {runs, wickets, batters_runs, bowlers_runs}
        # Current over scratch
        self.current_over_runs_off_bat: int = 0
        self.current_over_batters_runs: int = 0
        self.current_over_bowlers_runs: int = 0

        # Timeline
        self.timeline: list[str] = []

        # Wagon wheel data
        self.wagon_wheel_data: list[BallDirection] = []

        # Win probability tracking
        self.win_prob_history: list[dict] = (
            []
        )  # {ball_number, prob_batting, prob_bowling}

        # Current delivery state
        self.pending_delivery: str | None = None
        self.pending_delivery_label: str | None = None
        self.pending_delivery_speed_mod: float = 0.0
        self.current_over_balls: int = 0
        self.current_over_runs: int = 0
        self._over_ended_flag: bool = False
        self.phase: str = "pending"
        self.match_finished: bool = False
        self.winner_id: int | None = None
        self.cancel_task: asyncio.Task[None] | None = None
        # Substitution control
        self.swap_used: dict[int, bool] = {}
        self.sub_in_used: dict[int, set[str]] = {}
        self.bowlers_introduced: set[int] = set()

        # Last-ball wicket tracking (for IPL-style Impact: auto-replace dismissed batter)
        self.last_ball_was_wicket: bool = False
        self.last_wicket_dismissed_name: str = ""
        self.last_wicket_dismissed_idx: int = -1

        # Two-Way DRS Reviews Tracker
        self.batting_team_reviews: dict[int, int] = {challenger.id: 2, opponent.id: 2}
        self.bowling_team_reviews: dict[int, int] = {challenger.id: 2, opponent.id: 2}
        self.last_was_no_ball: bool = False
        self.prev_ball: str | None = None
        self.recent_wickets: list[int] = []

        self.captains: dict[int, MatchPlayer | None] = {}
        self.collapse_active: bool = False

        # Innings-specific batter/bowler stats for summary
        self.innings_batting_stats: list[dict] = []
        self.innings_bowling_stats: list[dict] = []
        self.pending_delivery_internal: str | None = None
        self.partnership_runs: int = 0
        self.partnership_balls: int = 0
        self.balls_since_wicket: int = 999
        self.consecutive_dots: int = 0
        self.pending_free_hit: bool = False

    @property
    def batting_user(self):
        return (
            self.challenger
            if self.batting_user_id == self.challenger.id
            else self.opponent
        )

    @property
    def bowling_user(self):
        return (
            self.challenger
            if self.bowling_user_id == self.challenger.id
            else self.opponent
        )

    @property
    def current_runs(self) -> int:
        return self.runs[self.innings - 1]

    @property
    def current_wickets(self) -> int:
        return self.wickets[self.innings - 1]

    @property
    def current_legal_balls(self) -> int:
        return self.legal_balls[self.innings - 1]

    @property
    def team1_name(self) -> str:
        return (
            self.challenger.display_name
            if self.innings1_batting_user_id == self.challenger.id
            else self.opponent.display_name
        )

    @property
    def team2_name(self) -> str:
        return (
            self.opponent.display_name
            if self.innings1_batting_user_id == self.challenger.id
            else self.challenger.display_name
        )

    @property
    def t1_total_runs(self) -> int:
        return self.runs[0] if len(self.runs) > 0 else 0

    @property
    def t1_total_wickets(self) -> int:
        return self.wickets[0] if len(self.wickets) > 0 else 0

    @property
    def t2_total_runs(self) -> int:
        return (
            self.runs[1]
            if len(self.runs) > 1
            else self.current_runs if self.innings == 2 else 0
        )

    @property
    def t2_total_wickets(self) -> int:
        return (
            self.wickets[1]
            if len(self.wickets) > 1
            else self.current_wickets if self.innings == 2 else 0
        )

    @property
    def t1_batsmen(self) -> list[dict]:
        team = self.teams.get(self.innings1_batting_user_id, [])
        return [
            {
                "name": p.name,
                "runs": self.batsman_stats.get(self._player_key(p), {}).get("r", 0),
            }
            for p in team
        ]

    @property
    def t2_bowlers(self) -> list[dict]:
        team_id = (
            self.opponent.id
            if self.innings1_batting_user_id == self.challenger.id
            else self.challenger.id
        )
        team = self.teams.get(team_id, [])
        return [
            {
                "name": p.name,
                "wickets": self.bowler_stats.get(p.name, {}).get("w", 0),
                "runs_conceded": self.bowler_stats.get(p.name, {}).get("r", 0),
            }
            for p in team
        ]

    @property
    def t2_batsmen(self) -> list[dict]:
        team_id = (
            self.opponent.id
            if self.innings1_batting_user_id == self.challenger.id
            else self.challenger.id
        )
        team = self.teams.get(team_id, [])
        return [
            {
                "name": p.name,
                "runs": self.batsman_stats.get(self._player_key(p), {}).get("r", 0),
            }
            for p in team
        ]

    @property
    def t1_bowlers(self) -> list[dict]:
        team = self.teams.get(self.innings1_batting_user_id, [])
        return [
            {
                "name": p.name,
                "wickets": self.bowler_stats.get(p.name, {}).get("w", 0),
                "runs_conceded": self.bowler_stats.get(p.name, {}).get("r", 0),
            }
            for p in team
        ]

    @property
    def is_innings_2(self) -> bool:
        return self.innings == 2

    @property
    def target_runs(self) -> int:
        return self.runs[0] + 1 if len(self.runs) > 0 else 0

    def _player_key(self, p: MatchPlayer | None) -> str:
        if not p:
            return ""
        if isinstance(p, dict):
            return str(p.get("name", ""))
        if p.inst_pk:
            for uid, team in self.teams.items():
                for mp in team:
                    if mp.inst_pk == p.inst_pk:
                        return f"{uid}:{p.inst_pk}"
            return str(p.inst_pk)
        return f"name:{p.name}"

    def reset_bat_stats_for_player(self, p: MatchPlayer) -> None:
        key = self._player_key(p)
        if key:
            self.batsman_stats[key] = {"r": 0, "b": 0, "4s": 0, "6s": 0}

    def _ensure_bat_stats_for_player(self, p: MatchPlayer | None) -> None:
        key = self._player_key(p)
        if key:
            self.batsman_stats.setdefault(key, {"r": 0, "b": 0, "4s": 0, "6s": 0})

    def _ensure_bowl_stats(self, name: str) -> None:
        self.bowler_stats.setdefault(name, {"b": 0, "r": 0, "w": 0, "m": 0})

    def add_runs(self, r: int) -> None:
        self.runs[self.innings - 1] += r
        if self.striker:
            self._ensure_bat_stats_for_player(self.striker)
            sk = self._player_key(self.striker)
            stats = self.batsman_stats[sk]
            stats["r"] = stats.get("r", stats.get("runs", 0)) + r
            stats["runs"] = stats["r"]
            if r == 4:
                stats["4s"] = stats.get("4s", stats.get("fours", 0)) + 1
                stats["fours"] = stats["4s"]
            elif r == 6:
                stats["6s"] = stats.get("6s", stats.get("sixes", 0)) + 1
                stats["sixes"] = stats["6s"]
        if self.current_bowler:
            name = _pname(self.current_bowler)
            self._ensure_bowl_stats(name)
            stats = self.bowler_stats[name]
            stats["r"] = stats.get("r", stats.get("runs", 0)) + r
            stats["runs"] = stats["r"]
        self.current_over_runs += r
        self.current_over_batters_runs += r
        self.partnership_runs += r
        if self.current_partnership:
            self.current_partnership.add_runs(r)

    def add_extra_runs(self, r: int, charge_bowler: bool = False) -> None:
        self.runs[self.innings - 1] += r
        self.current_over_runs += r
        if charge_bowler and self.current_bowler:
            name = _pname(self.current_bowler)
            self._ensure_bowl_stats(name)
            stats = self.bowler_stats[name]
            stats["r"] = stats.get("r", stats.get("runs", 0)) + r
            stats["runs"] = stats["r"]

    def add_legal_ball(self) -> None:
        self.legal_balls[self.innings - 1] += 1
        self.current_over_balls += 1
        if self.striker:
            self._ensure_bat_stats_for_player(self.striker)
            stats = self.batsman_stats[self._player_key(self.striker)]
            stats["b"] = stats.get("b", stats.get("balls", 0)) + 1
            stats["balls"] = stats["b"]
        if self.current_bowler:
            name = _pname(self.current_bowler)
            self._ensure_bowl_stats(name)
            stats = self.bowler_stats[name]
            stats["b"] = stats.get("b", stats.get("balls", 0)) + 1
            stats["balls"] = stats["b"]
            self.bowler_ball_count[name] = self.bowler_ball_count.get(name, 0) + 1
        if self.current_partnership:
            self.current_partnership.add_ball()
        self.partnership_balls += 1
        self.balls_since_wicket += 1

    def add_wicket(
        self, dismissal_type: str | None = None, batsman: MatchPlayer | None = None
    ) -> None:
        self.wickets[self.innings - 1] += 1
        bowler_credit = dismissal_type in (
            "Bowled",
            "LBW",
            "Caught",
            "Stumped",
            "Hit Wicket",
        )
        if self.current_bowler and bowler_credit:
            name = _pname(self.current_bowler)
            self._ensure_bowl_stats(name)
            stats = self.bowler_stats[name]
            stats["w"] = stats.get("w", stats.get("wickets", 0)) + 1
            stats["wickets"] = stats["w"]
        dismissed = batsman or self.striker
        if dismissed:
            dismissed_name = _pname(dismissed)
            self.dismissed.append(dismissed_name)
            self.last_ball_was_wicket = True
            self.last_wicket_dismissed_name = dismissed_name
            self.last_wicket_dismissed_idx = -1
            team = self._team_players(self.teams.get(self.batting_user_id))
            for idx, p in enumerate(team):
                if _pname(p) == dismissed_name:
                    self.last_wicket_dismissed_idx = idx
                    break
            if self.striker and _pname(self.striker) == dismissed_name:
                self.striker = None
            if self.non_striker and _pname(self.non_striker) == dismissed_name:
                self.non_striker = None
            logger.info(
                "[wicket] dismissed=%s striker=%s non_striker=%s",
                dismissed_name,
                _pname(self.striker),
                _pname(self.non_striker),
            )
            self.balls_since_wicket = 0
        if self.current_partnership:
            self.current_partnership.dismissal = dismissal_type or "Unknown"
            self.partnership_history.append(self.current_partnership)
            self.current_partnership = None

    def rotate_strike(self) -> None:
        self.striker, self.non_striker = self.non_striker, self.striker

    def end_over(self) -> None:
        if self._over_ended_flag:
            return
        self._over_ended_flag = True
        self.rotate_strike()
        if self.striker is None and self.non_striker is not None:
            self.striker, self.non_striker = self.non_striker, self.striker
        legal_balls = getattr(self, "current_over_balls", 0)
        if self.current_bowler and legal_balls == 6 and self.current_over_runs == 0:
            self.bowler_maidens[self.current_bowler.name] = (
                self.bowler_maidens.get(self.current_bowler.name, 0) + 1
            )
            self.bowler_stats[self.current_bowler.name]["m"] = self.bowler_maidens[
                self.current_bowler.name
            ]
        self.over_history.append(
            {
                "runs": self.current_over_runs,
                "wickets": 0,
                "batters_runs": self.current_over_batters_runs,
                "bowlers_runs": self.current_over_bowlers_runs,
            }
        )
        is_wicket_over = False
        if self.over_history:
            recent = (
                self.timeline[-self.current_over_balls :]
                if self.current_over_balls
                else []
            )
            is_wicket_over = "W" in recent
        if is_wicket_over:
            self.over_history[-1]["wickets"] = 1
        self.last_over_bowler = (
            self.current_bowler.name if self.current_bowler else None
        )
        self.current_over_balls = 0
        self.current_over_runs = 0
        self.current_over_runs_off_bat = 0
        self.current_over_batters_runs = 0
        self.current_over_bowlers_runs = 0
        self.current_bowler = None

    def apply_ball_result(
        self, outcome_dict: dict, batsman: MatchPlayer | None = None
    ) -> None:
        outcome_str = outcome_dict.get("outcome_str", "0")
        runs = outcome_dict.get("runs_batter", 0)
        overthrows = outcome_dict.get("overthrows", 0)
        is_extra = outcome_dict.get("extra_type") is not None
        extra_type = outcome_dict.get("extra_type")
        dismissal_type = outcome_dict.get("dismissal_type")
        is_wicket = outcome_str == "W" or dismissal_type in (
            "Bowled",
            "LBW",
            "Caught",
            "Stumped",
        )
        free_hit_saved = outcome_dict.get("free_hit_saved", False)

        self.timeline.append(outcome_str)
        self.record_ball(outcome_str)

        if is_extra:
            self.last_was_no_ball = extra_type == "NB"
            if extra_type == "Wd":
                self.runs[self.innings - 1] += 1
                self.current_over_runs += 1
                if self.current_bowler:
                    self._ensure_bowl_stats(self.current_bowler.name)
                    self.bowler_stats[self.current_bowler.name]["r"] += 1
            elif extra_type == "NB":
                nb_runs = runs + overthrows
                self.runs[self.innings - 1] += 1 + nb_runs
                self.current_over_runs += 1 + nb_runs
                if nb_runs > 0 and self.striker:
                    self._ensure_bat_stats_for_player(self.striker)
                    sk = self._player_key(self.striker)
                    self.batsman_stats[sk]["r"] += nb_runs
                    if nb_runs == 4:
                        self.batsman_stats[sk]["4s"] += 1
                    elif nb_runs == 6:
                        self.batsman_stats[sk]["6s"] += 1
                if self.current_bowler:
                    self._ensure_bowl_stats(self.current_bowler.name)
                    self.bowler_stats[self.current_bowler.name]["r"] += nb_runs
            elif extra_type in ("B", "LB"):
                bye_runs = outcome_dict.get("runs_extras", 0)
                self.runs[self.innings - 1] += bye_runs
                self.current_over_runs += bye_runs
                if self.striker:
                    self._ensure_bat_stats_for_player(self.striker)
                    self.batsman_stats[self._player_key(self.striker)]["b"] += 1
                if self.current_bowler:
                    self._ensure_bowl_stats(self.current_bowler.name)
                    self.bowler_stats[self.current_bowler.name]["b"] += 1
                    self.bowler_stats[self.current_bowler.name]["r"] += bye_runs
                    self.bowler_ball_count[self.current_bowler.name] = (
                        self.bowler_ball_count.get(self.current_bowler.name, 0) + 1
                    )
                self.legal_balls[self.innings - 1] += 1
                self.current_over_balls += 1
                if self.current_partnership:
                    self.current_partnership.runs += bye_runs
                    self.current_partnership.add_ball()
        else:
            self.last_was_no_ball = False
            self.add_legal_ball()
            if is_wicket and dismissal_type not in ("Run Out", None):
                dismissed_runs = runs + overthrows
                self.runs[self.innings - 1] += dismissed_runs
                self.current_over_runs += dismissed_runs
                self.current_over_runs_off_bat += dismissed_runs
                self.current_over_bowlers_runs += dismissed_runs
                if self.striker:
                    self._ensure_bat_stats_for_player(self.striker)
                    sk = self._player_key(self.striker)
                    self.batsman_stats[sk]["r"] += dismissed_runs
                if self.current_bowler:
                    self._ensure_bowl_stats(self.current_bowler.name)
                    self.bowler_stats[self.current_bowler.name]["r"] += dismissed_runs
                if self.current_partnership:
                    self.current_partnership.runs += dismissed_runs
            else:
                self.add_runs(runs + overthrows)

        dismissed = batsman or self.striker
        if is_wicket and dismissal_type not in ("Run Out", None) and not free_hit_saved:
            self.add_wicket(dismissal_type, batsman=dismissed)
        elif dismissal_type == "Run Out" and not free_hit_saved:
            self.add_wicket("Run Out", batsman=dismissed)

    def _do_record_boundary_direction(
        self, runs: int, delivery: str, shot: str
    ) -> None:
        if runs not in (4, 6):
            return
        striker_name = self.striker.name if self.striker else "?"
        bowler_name = self.current_bowler.name if self.current_bowler else "?"
        direction = self._choose_direction(delivery, shot, runs)
        bw = BallDirection(
            over=self.current_over_balls // 6 + 1,
            ball=self.current_over_balls % 6 + 1,
            batsman=striker_name,
            bowler=bowler_name,
            runs=runs,
            direction=direction,
            shot=shot,
        )
        self.wagon_wheel_data.append(bw)

    def _choose_direction(self, delivery: str, shot: str, runs: int) -> str:
        direction_bias = {
            "Fine Leg": "5 o'clock",
            "Square Leg": "6 o'clock",
            "Mid-wicket": "7 o'clock",
            "Long-on": "8 o'clock",
            "Mid-on": "9 o'clock",
            "Cover": "10 o'clock",
            "Point": "11 o'clock",
            "Third Man": "12 o'clock",
            "Fine Third": "1 o'clock",
            "Deep Cover": "2 o'clock",
            "Extra Cover": "3 o'clock",
        }
        if shot == "Pull":
            return random.choice(
                ["5 o'clock", "6 o'clock", "7 o'clock", "Square Leg", "Fine Leg"]
            )
        elif shot == "Sweep":
            return random.choice(["Square Leg", "6 o'clock", "7 o'clock", "Mid-wicket"])
        elif shot == "Cut":
            return random.choice(["Point", "11 o'clock", "10 o'clock", "Third Man"])
        elif shot in ("Drive", "Lofted"):
            return random.choice(
                ["Cover", "10 o'clock", "9 o'clock", "Extra Cover", "Mid-on"]
            )
        elif shot == "Flick":
            return random.choice(["Mid-wicket", "7 o'clock", "Square Leg", "Fine Leg"])
        elif shot in ("Defend", "Back-foot"):
            return random.choice(["9 o'clock", "10 o'clock", "11 o'clock"])
        elif shot == "Front-foot":
            return random.choice(["9 o'clock", "10 o'clock", "Cover"])
        elif shot == "Reverse-Sweep":
            return random.choice(["Third Man", "12 o'clock", "1 o'clock", "Fine Third"])
        else:
            return random.choice(list(direction_bias.values()))

    def calculate_win_probability(self) -> tuple[float, float]:
        """Returns (batting_team_prob, bowling_team_prob) using simplified DLS-inspired model."""
        inn = self.innings - 1
        current_runs = self.runs[inn]
        wickets = self.wickets[inn]
        legal_balls = self.legal_balls[inn]
        total_balls = self.overs * 6

        if self.innings == 1:
            target = current_runs + 1
            target_balls = total_balls
            batting_prob = 0.5
        else:
            target = self.runs[0] + 1
            target_balls = total_balls
            runs_needed = max(0, target - current_runs)
            balls_remaining = max(1, total_balls - legal_balls)
            wickets_in_hand = max(0, 10 - wickets)
            # Par score resource model (simplified)
            max_possible = wickets_in_hand * 36 + balls_remaining
            if max_possible <= 0:
                batting_prob = 0.0 if runs_needed > 0 else 1.0
            else:
                batting_prob = min(0.99, max(0.01, runs_needed / max_possible))
                if runs_needed == 0:
                    batting_prob = 1.0
                elif balls_remaining == 0:
                    batting_prob = 0.0

        bowling_prob = 1.0 - batting_prob
        if self.innings == 1:
            batting_prob = 0.5
            bowling_prob = 0.5
        return round(batting_prob, 3), round(bowling_prob, 3)

    def record_win_prob(self, ball_number: int) -> None:
        bat_p, bowl_p = self.calculate_win_probability()
        self.win_prob_history.append(
            {
                "ball": ball_number,
                "batting": bat_p,
                "bowling": bowl_p,
            }
        )

    def overs_str(self) -> str:
        b = self.current_legal_balls
        return f"{b // 6}.{b % 6}"

    def bowler_overs_str(self, name: str) -> str:
        b = self.bowler_ball_count.get(name, 0)
        return f"{b // 6}.{b % 6}"

    def sr(self, name: str) -> str:
        s = self.batsman_stats.get(name, {})
        r, b = s.get("r", 0), s.get("b", 0)
        return f"{(r / b) * 100:.1f}" if b > 0 else ""

    def sr_for_player(self, p: MatchPlayer) -> str:
        key = self._player_key(p)
        s = self.batsman_stats.get(key, {})
        r, b = s.get("r", 0), s.get("b", 0)
        return f"{(r / b) * 100:.1f}" if b > 0 else ""

    def crr(self) -> float:
        lb = self.current_legal_balls
        return round((self.current_runs / lb) * 6, 2) if lb else 0.0

    def rrr(self) -> float:
        if self.innings != 2:
            return 0.0
        needed = (self.runs[0] + 1) - self.current_runs
        balls_left = (self.overs * 6) - self.current_legal_balls
        if balls_left <= 0 or needed <= 0:
            return 0.0
        return round((needed / balls_left) * 6, 2)

    def target(self) -> int | None:
        return self.runs[0] + 1 if self.innings == 2 else None

    def projected_score(self) -> int:
        balls_left = (self.overs * 6) - self.current_legal_balls
        return self.current_runs + int(self.crr() * balls_left / 6)

    def projected(self) -> int:
        """Legacy scoreboard alias retained for the original views module."""
        return self.projected_score()

    def balls_remaining(self) -> int:
        return max(0, (self.overs * 6) - self.current_legal_balls)

    def max_bowler_balls(self) -> int:
        return max(1, math.ceil(self.overs / 5)) * 6

    def get_batting_team(self) -> dict:
        return self.teams.get(self.batting_user_id, {})

    def get_bowling_team(self) -> dict:
        return self.teams.get(self.bowling_user_id, {})

    @staticmethod
    def _team_players(team) -> list:
        return team.get("players", []) if isinstance(team, dict) else list(team or [])

    @staticmethod
    def _player_value(player, key: str, default=0):
        return player.get(key, default) if isinstance(player, dict) else getattr(player, key, default)

    def get_available_bowlers(self) -> list:
        team = self.teams.get(self.bowling_user_id, [])
        max_b = self.max_bowler_balls()
        players = self._team_players(team)
        avail = [
            p
            for p in players
            if (
                (p.can_bowl() if hasattr(p, "can_bowl") else (
                    self._player_value(p, "bowling_type") is not None
                    or self._player_value(p, "bowl", 0) >= 60
                ))
                and self.bowler_ball_count.get(_pname(p), 0) < max_b
                and _pname(p) != self.last_over_bowler
            )
        ]
        if not avail:
            avail = [
                p
                for p in players
                if (p.can_bowl() if hasattr(p, "can_bowl") else (
                    self._player_value(p, "bowling_type") is not None
                    or self._player_value(p, "bowl", 0) >= 60
                ))
                and self.bowler_ball_count.get(_pname(p), 0) < max_b
            ]
        if not avail:
            avail = [p for p in players if (
                p.can_bowl() if hasattr(p, "can_bowl") else (
                    self._player_value(p, "bowling_type") is not None
                    or self._player_value(p, "bowl", 0) >= 60
                )
            )]
        if not avail:
            avail = sorted(players, key=lambda p: self._player_value(p, "bowl", 0), reverse=True)[:3]
        return avail

    def get_available_batsmen(self) -> list:
        team = self.teams.get(self.batting_user_id, [])
        players = self._team_players(team)
        currently_batting = set()
        if self.striker:
            currently_batting.add(_pname(self.striker))
        if self.non_striker:
            currently_batting.add(_pname(self.non_striker))
        available = []
        for p in players:
            if _pname(p) in self.dismissed or _pname(p) in currently_batting:
                continue
            available.append(p)
        return available

    def is_innings_over(self) -> bool:
        if self.current_wickets >= 10:
            return True
        if self.current_legal_balls >= self.overs * 6:
            return True
        if self.innings == 2 and self.current_runs >= self.runs[0] + 1:
            return True
        return False

    def start_second_innings(self) -> None:
        self.batting_user_id, self.bowling_user_id = (
            self.bowling_user_id,
            self.batting_user_id,
        )
        self.striker = None
        self.non_striker = None
        self.current_bowler = None
        self.dismissed = []
        self.bowler_ball_count = {}
        self.bowler_maidens = {}
        self.bowler_stats = {}
        self.batsman_stats = {}
        self._over_ended_flag = False
        self.last_ball_was_wicket = False
        self.last_wicket_dismissed_name = ""
        self.last_wicket_dismissed_idx = -1
        self.last_over_bowler = None
        self.current_partnership = None
        self.timeline = []
        self.current_over_balls = 0
        self.current_over_runs = 0
        self.current_over_runs_off_bat = 0
        self.over_history = []
        self.wagon_wheel_data = []
        self.win_prob_history = []
        self.last_was_no_ball = False
        self.innings = 2
        self.phase = "select opener"
        # Refresh DRS reviews for the second innings
        for uid in self.teams.keys():
            self.batting_team_reviews[uid] = 2
            self.bowling_team_reviews[uid] = 2

    async def build_team_list(self, user_id: int, instances) -> list[MatchPlayer]:
        from engine import (
            apply_lor_bonus,
            apply_memories_boost,
            apply_potm_boost,
            apply_premium_signature_boost,
            apply_shiny_boost,
            get_card_role,
            get_cricket_stats,
            get_memories_player_type,
            get_narrative_attributes,
            has_lor_bonus,
            is_memories_event,
            is_potm_event,
            is_premium_or_signature_event,
            is_shiny,
        )

        team = []
        for inst in instances:
            if isinstance(inst, MatchPlayer):
                team.append(inst)
                continue
            try:
                ovr, bat, bowl = get_cricket_stats(inst.ball)
                role = get_card_role(inst.ball)
                name = inst.ball.country
                pk = inst.pk
                btype = (getattr(inst.ball, "capacity_logic", None) or {}).get(
                    "bowling_type"
                )
                bat_style = (getattr(inst.ball, "capacity_logic", None) or {}).get(
                    "bat_style", "balanced"
                )
                # Use badge_rarity from capacity_logic (lower = better), not spawn_chance (ball.rarity)
                logic_dict = getattr(inst.ball, "capacity_logic", None) or {}
                badge_r = logic_dict.get("badge_rarity")
                rarity_val = (
                    float(badge_r)
                    if badge_r is not None
                    else (getattr(inst.ball, "rarity", 1.0) or 1.0)
                )
                if has_lor_bonus(inst.ball):
                    bat, bowl = apply_lor_bonus(bat, bowl)
                if is_shiny(inst):
                    ovr, bat, bowl = apply_shiny_boost(ovr, bat, bowl)
                if is_premium_or_signature_event(inst):
                    ovr, bat, bowl = apply_premium_signature_boost(ovr, bat, bowl)
                if is_memories_event(inst):
                    ovr, bat, bowl = apply_memories_boost(ovr, bat, bowl)
                if is_potm_event(inst):
                    ovr, bat, bowl = apply_potm_boost(ovr, bat, bowl)
                memories_type = get_memories_player_type(inst)
                fielder_grade = getattr(inst.ball, "fielder_grade", 1) or 1
                narrative_attrs = get_narrative_attributes(inst.ball)
            except Exception:
                ovr, bat, bowl, role = 0, 0, 0, "Bat"
                name = "Unknown"
                pk = getattr(inst, "pk", 0)
                btype = None
                bat_style = "balanced"
                rarity_val = 1.0
                memories_type = None
                fielder_grade = 1
                narrative_attrs = []
            team.append(
                MatchPlayer(
                    name=name,
                    ovr=ovr,
                    bat=bat,
                    bowl=bowl,
                    bowling_type=btype,
                    role=role,
                    inst_pk=pk,
                    bat_style=bat_style,
                    rarity=rarity_val,
                    memories_type=memories_type,
                    fielder_grade=fielder_grade,
                    narrative_attrs=narrative_attrs,
                )
            )
        self.teams[user_id] = team
        self.team_pks[user_id] = [mp.inst_pk for mp in team]
        return team

    def record_ball(self, outcome: str) -> None:
        self.last_ball_was_wicket = False
        self.prev_ball = outcome
        is_wicket = 1 if outcome == "W" else 0
        self.recent_wickets.append(is_wicket)
        if len(self.recent_wickets) > 12:
            self.recent_wickets.pop(0)
        self.collapse_active = sum(self.recent_wickets[-12:]) >= 2

    def get_captain(self, user_id: int) -> MatchPlayer | None:
        return self.captains.get(user_id)

    def compute_match_stats(self) -> dict:
        self._set_winner()
        batting_user_id = self.batting_user_id
        bowling_user_id = self.bowling_user_id

        user_updates: dict[int, dict] = {}
        for uid in (self.challenger.id, self.opponent.id):
            is_winner = uid == self.winner_id
            user_updates[uid] = {
                "matches_played": 1,
                "matches_won": 1 if is_winner else 0,
                "matches_lost": (
                    1 if not is_winner and self.winner_id is not None else 0
                ),
                "runs_scored": 0,
                "wickets_taken": 0,
                "total_runs_conceded": 0,
                "total_maiden_overs_bowled": 0,
                "best_batting_innings_score": 0,
                "best_bowling_wickets": None,
                "best_bowling_runs": None,
                "total_half_centuries": 0,
                "total_centuries": 0,
                "total_double_centuries": 0,
            }

        card_updates: dict[int, dict] = {}

        for key, bs in self.batsman_stats.items():
            runs = bs.get("r", 0)
            balls = bs.get("b", 0)
            fours = bs.get("4s", 0)
            sixes = bs.get("6s", 0)
            uid = self._batsman_owner(key)
            inst_pk = self._batsman_inst_pk(key)
            if uid and inst_pk:
                d = card_updates.setdefault(
                    inst_pk,
                    {
                        "matches_played": 1,
                        "runs_scored": 0,
                        "wickets_taken": 0,
                        "maiden_overs_bowled": 0,
                        "highest_score": 0,
                        "best_bowling_wickets": 0,
                        "best_bowling_runs": 999,
                    },
                )
                d["runs_scored"] = d.get("runs_scored", 0) + runs
                if runs > d.get("highest_score", 0):
                    d["highest_score"] = runs
                if runs >= 200:
                    d["double_centuries"] = d.get("double_centuries", 0) + 1
                elif runs >= 100:
                    d["centuries"] = d.get("centuries", 0) + 1
                elif runs >= 50:
                    d["half_centuries"] = d.get("half_centuries", 0) + 1
                if uid in user_updates:
                    user_updates[uid]["runs_scored"] += runs
                    if runs > user_updates[uid]["best_batting_innings_score"]:
                        user_updates[uid]["best_batting_innings_score"] = runs
                    if runs >= 200:
                        user_updates[uid]["total_double_centuries"] += 1
                    elif runs >= 100:
                        user_updates[uid]["total_centuries"] += 1
                    elif runs >= 50:
                        user_updates[uid]["total_half_centuries"] += 1

        for name, bws in self.bowler_stats.items():
            wkts = bws.get("w", 0)
            runs_given = bws.get("r", 0)
            overs = bws.get("b", 0) / 6
            maidens = bws.get("m", 0)
            bowler_pk = self._bowler_inst_pk(name)
            uid = self._bowler_owner(name)
            if uid in user_updates:
                user_updates[uid]["wickets_taken"] += wkts
                user_updates[uid]["total_runs_conceded"] += runs_given
                user_updates[uid]["total_maiden_overs_bowled"] += maidens
                if wkts > 0:
                    if (
                        user_updates[uid]["best_bowling_wickets"] is None
                        or wkts > user_updates[uid]["best_bowling_wickets"]
                    ):
                        user_updates[uid]["best_bowling_wickets"] = wkts
                        user_updates[uid]["best_bowling_runs"] = runs_given
                    elif (
                        wkts == user_updates[uid]["best_bowling_wickets"]
                        and runs_given < user_updates[uid]["best_bowling_runs"]
                    ):
                        user_updates[uid]["best_bowling_runs"] = runs_given
            if bowler_pk:
                d = card_updates.setdefault(
                    bowler_pk,
                    {
                        "matches_played": 0,
                        "runs_scored": 0,
                        "wickets_taken": 0,
                        "maiden_overs_bowled": 0,
                        "highest_score": 0,
                        "best_bowling_wickets": 0,
                        "best_bowling_runs": 999,
                    },
                )
                d["wickets_taken"] = d.get("wickets_taken", 0) + wkts
                if wkts > d.get("best_bowling_wickets", 0):
                    d["best_bowling_wickets"] = wkts
                    d["best_bowling_runs"] = runs_given
                elif wkts == d.get("best_bowling_wickets", 0) and runs_given < d.get(
                    "best_bowling_runs", 999
                ):
                    d["best_bowling_runs"] = runs_given
                d["maiden_overs_bowled"] = d.get("maiden_overs_bowled", 0) + maidens

        return {
            "user_updates": {
                uid: v
                for uid, v in user_updates.items()
                if any(
                    v.get(k, 0)
                    for k in (
                        "runs_scored",
                        "wickets_taken",
                        "matches_played",
                        "total_runs_conceded",
                        "total_maiden_overs_bowled",
                        "total_centuries",
                        "total_half_centuries",
                        "total_double_centuries",
                    )
                )
                or v.get("best_bowling_wickets")
            },
            "card_updates": {
                pk: v
                for pk, v in card_updates.items()
                if any(
                    v.get(k, 0)
                    for k in (
                        "runs_scored",
                        "wickets_taken",
                        "matches_played",
                        "maiden_overs_bowled",
                        "highest_score",
                        "best_bowling_wickets",
                        "centuries",
                        "half_centuries",
                        "double_centuries",
                    )
                )
            },
        }

    def _set_winner(self) -> None:
        if self.runs[1] > self.runs[0]:
            self.winner_id = self.batting_user_id
        elif self.runs[0] > self.runs[1]:
            self.winner_id = self.bowling_user_id
        else:
            self.winner_id = None

    def populate_summary_stats(self) -> None:
        def _display_name_for_key(game, key: str) -> str:
            try:
                uid_str, _, pk_str = key.partition(":")
                if uid_str and pk_str:
                    uid = int(uid_str)
                    inst_pk = int(pk_str)
                    for mp in game.teams.get(uid, []):
                        if getattr(mp, "inst_pk", 0) == inst_pk:
                            return mp.name
            except (ValueError, AttributeError):
                pass
            return ""

        t1_bat_uid = self.innings1_batting_user_id
        t2_bowl_uid = (
            self.opponent.id
            if self.innings1_batting_user_id == self.challenger.id
            else self.challenger.id
        )

        bat_records = []
        bowl_records = []
        for key, bs in self.batsman_stats.items():
            uid = self._batsman_owner(key)
            pks_in_uid_team = [mp.inst_pk for mp in self.teams.get(uid, [])]
            inst_pk = self._batsman_inst_pk(key)
            if uid not in self.teams:
                continue
            name = _display_name_for_key(self, key)
            if not name or name == key:
                for mp in self.teams.get(uid, []):
                    if mp.inst_pk == inst_pk:
                        name = mp.name
                        break
            r = bs.get("r", 0)
            b = bs.get("b", 0)
            fours = bs.get("4s", 0)
            sixes = bs.get("6s", 0)
            sr_val = round((r / b) * 100, 1) if b else 0.0
            bat_records.append(
                {
                    "name": name,
                    "runs": r,
                    "balls": b,
                    "4s": fours,
                    "6s": sixes,
                    "sr": sr_val,
                    "inst_pk": inst_pk,
                }
            )

        for name, bws in self.bowler_stats.items():
            uid = self._bowler_owner(name)
            if uid not in self.teams:
                continue
            wkts = bws.get("w", 0)
            runs_given = bws.get("r", 0)
            balls = bws.get("b", 0)
            maidens = bws.get("m", 0)
            overs_f = balls / 6.0
            econ = round(runs_given / max(overs_f, 0.1), 2) if overs_f else 0.0
            bowl_records.append(
                {
                    "name": name,
                    "wickets": wkts,
                    "runs_conceded": runs_given,
                    "overs": overs_f,
                    "maidens": maidens,
                    "econ": econ,
                }
            )

        idx = 0 if self.innings == 1 else 1
        while len(self.innings_batting_stats) <= idx:
            self.innings_batting_stats.append([])
        self.innings_batting_stats[idx] = bat_records
        while len(self.innings_bowling_stats) <= idx:
            self.innings_bowling_stats.append([])
        self.innings_bowling_stats[idx] = bowl_records

    def _batsman_key(self, p: MatchPlayer | None) -> int | None:
        if not p or not p.inst_pk:
            return None
        for uid, team in self.teams.items():
            for mp in team:
                if mp.inst_pk == p.inst_pk:
                    return p.inst_pk
        return None

    def _player_uid(self, key: str) -> int | None:
        if not key or ":" not in key:
            return None
        try:
            return int(key.split(":", 1)[0])
        except ValueError:
            return None

    def _batsman_owner(self, key: str) -> int | None:
        return self._player_uid(key)

    def _batsman_inst_pk(self, key: str) -> int | None:
        if ":" in key:
            try:
                return int(key.split(":", 1)[1])
            except ValueError:
                return None
        return None

    def _bowler_owner(self, name: str) -> int | None:
        for uid, team in self.teams.items():
            for mp in team:
                if mp.name == name:
                    return uid
        return None

    def _bowler_inst_pk(self, name: str) -> int | None:
        for uid, team in self.teams.items():
            for mp in team:
                if mp.name == name and mp.inst_pk:
                    return mp.inst_pk
        return None

    def match_result(self) -> str:
        t1, t2 = self.runs[0], self.runs[1]
        if self.innings1_batting_user_id == self.challenger.id:
            team_first = self.challenger.display_name
            team_second = self.opponent.display_name
        else:
            team_first = self.opponent.display_name
            team_second = self.challenger.display_name
        if t2 > t1:
            wkts_left = 10 - self.wickets[1]
            balls_left = self.balls_remaining()
            return (
                f"**{team_second}** won by **{wkts_left}** wicket{'s' if wkts_left != 1 else ''} "
                f"({balls_left} ball{'s' if balls_left != 1 else ''} remaining)!"
            )
        elif t1 > t2:
            diff = t1 - t2
            return f"**{team_first}** won by **{diff}** run{'s' if diff != 1 else ''}!"
        else:
            return "Match tied! What a game!"
