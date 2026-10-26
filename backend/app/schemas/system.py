"""Named wire contracts for health, operator responses, and safe errors."""
from typing import Literal
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorOut(BaseModel):
    detail: ErrorDetail


class HealthOut(BaseModel):
    status: Literal["ok"]
    service: Literal["lolpick-backend"]
    version: str


class ReadyOut(BaseModel):
    status: Literal["ok"]
    database: Literal["connected"]
    migration: str


class SeasonActivatedOut(BaseModel):
    seasonId: str
    status: Literal["active"]
    initializedMembers: int


class SeasonFinishedOut(BaseModel):
    seasonId: str
    status: Literal["finished"]


class SyncSummaryOut(BaseModel):
    seasonId: str
    seriesId: int
    received: int
    created: int
    updated: int
    skipped_tbd: int
    stored_tbd: int
    stored_unscheduled: int
    skipped_unscheduled: int
    conflicts: int
    stale: int
    settled: int
    refunded: int
    settlement_blocked: int


class SettlementOut(BaseModel):
    matchId: str
    seasonId: str
    result: Literal["team_a_win", "team_b_win", "draw", "cancelled"] | None
    winner: str | None
    settlementStatus: Literal["pending", "settled", "refunded"]
    pool: int | None
    paid: int | None
    removedRemainder: int | None
