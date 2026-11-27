"""Core Pydantic data models for One Move (Task 1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Position = Literal["GKP", "DEF", "MID", "FWD"]


class PlayerSnapshot(BaseModel):
    """Compact representation of one squad player for UI rendering & Agent context."""

    id: int
    name: str
    team: str
    team_id: int
    pos: Position
    now_cost: float = Field(..., description="Current market price in £m (1 decimal)")
    selling_price: float = Field(..., description="Estimated or exact selling price in £m")
    price_is_estimated: bool = Field(
        ..., description="True if initial squad player with unknown purchase price"
    )
    status: str = Field(..., description="Official status: a/d/i/s/u/n")
    chance_of_playing: int | None = None
    news: str = ""
    form: float = 0.0
    ppg: float = 0.0
    next_3_fdr: list[str] = Field(default_factory=list)
    is_starter: bool
    bench_order: int = Field(..., ge=0, le=4, description="0 for starter, 1-4 for bench")
    is_captain: bool = False
    is_vice_captain: bool = False


class TeamSnapshot(BaseModel):
    """Compact team ledger & 15-player snapshot."""

    team_id: int
    team_name: str
    gw: int = Field(..., ge=1, le=38, description="Upcoming target Gameweek for decision")
    picks_gw: int = Field(..., ge=1, le=38, description="Gameweek of the fetched picks")
    started_event: int = Field(..., ge=1, le=38)
    bank: float = Field(..., ge=0.0, description="Money in the bank in £m")
    free_transfers: int = Field(..., ge=1, le=5)
    ft_is_calibrated: bool = False
    available_chips: list[str] = Field(default_factory=list)
    players: list[PlayerSnapshot]

    @field_validator("players")
    @classmethod
    def must_have_15_players(cls, value: list[PlayerSnapshot]) -> list[PlayerSnapshot]:
        if len(value) != 15:
            raise ValueError(f"Squad must contain exactly 15 players, got {len(value)}")
        return value


class UniversePlayer(BaseModel):
    """Full-league player entry stored locally in FPLUniverse (not dumped to LLM)."""

    id: int
    name: str
    full_name: str
    team_id: int
    team: str
    pos: Position
    now_cost: float
    status: str
    chance_of_playing: int | None = None
    news: str = ""
    form: float = 0.0
    ppg: float = 0.0
    total_points: int = 0
    minutes: int = 0
    selected_by_percent: float = 0.0
    defensive_contribution: int = 0
    next_5_fdr: list[str] = Field(default_factory=list)


class FixtureInfo(BaseModel):
    """Premier League fixture summary."""

    event: int | None = None
    team_h: int
    team_a: int
    team_h_difficulty: int
    team_a_difficulty: int
    finished: bool = False


class FPLUniverse(BaseModel):
    """Local player & fixture lookup pool for deterministic tools."""

    current_gw: int = Field(..., ge=1, le=38)
    next_gw: int = Field(..., ge=1, le=38)
    teams: dict[int, str]
    players: dict[int, UniversePlayer]
    fixtures: list[FixtureInfo]


class SnapshotBundle(BaseModel):
    """Combined output of Task 1: user-facing snapshot + local universe."""

    snapshot: TeamSnapshot
    universe: FPLUniverse
