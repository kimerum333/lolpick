from typing import Literal
from pydantic import BaseModel


class TeamOut(BaseModel):
    id: str
    name: str
    region: str = ""
    color: str = "#64748b"
    logo: str | None = None
    acronym: str | None = None
    location: str | None = None
    apiSource: str


class SeasonOut(BaseModel):
    id: str
    slug: str
    name: str
    year: int
    label: str
    active: bool
    status: Literal["upcoming", "active", "finished"]
    teams: list[str]
    initialPoints: int
    isDemo: bool
    startsAt: int | None = None
    endsAt: int | None = None


class EventStageOut(BaseModel):
    name: str
    startsAt: int | None
    endsAt: int | None
    matchCount: int
    unscheduledCount: int


class EventOut(SeasonOut):
    stages: list[EventStageOut]


class CompetitionOut(BaseModel):
    id: str
    name: str
    slug: str | None = None


class MatchOut(BaseModel):
    id: str
    event: str
    season: str
    round: str
    teams: tuple[str | None, str | None]
    teamDetails: tuple[TeamOut | None, TeamOut | None]
    pools: tuple[int, int]
    startsAt: int | None
    status: Literal["scheduled", "live", "finished", "settled", "cancelled"]
    score: tuple[int, int] | None
    winner: str | None
    bettingOpen: bool
    seasonStatus: Literal["upcoming", "active", "finished"] | None = None
    result: Literal["team_a_win", "team_b_win", "draw", "cancelled"] | None = None
    settlementStatus: Literal["pending", "settled", "refunded"] = "pending"
    settlementPool: int | None = None
    settlementPaid: int | None = None
    removedRemainder: int | None = None
    isDemo: bool
    poolSource: Literal["demo", "bets"]
    apiSource: str
    providerStatus: str | None = None
    apiSyncedAt: int | None = None
    name: str = ""
    matchType: str | None = None
    numberOfGames: int | None = None
    league: CompetitionOut | None = None
    tournament: CompetitionOut | None = None
    forfeit: bool = False
    draw: bool = False
    dataIssues: list[str] = []
    bettingClosedReason: str | None = None


class StakeRankOut(BaseModel):
    rank: int
    displayName: str | None = None
    amount: int
    shareBasisPoints: int
    sealHeld: bool


class StakeRankingOut(BaseModel):
    matchId: str
    seasonId: str
    teams: tuple[str | None, str | None]
    pools: tuple[int, int]
    items: tuple[list[StakeRankOut], list[StakeRankOut]]


class MatchPage(BaseModel):
    items: list[MatchOut]
    total: int
    limit: int
    offset: int
