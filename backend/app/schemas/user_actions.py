from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator
from app.db.domain import MAX_POINTS


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def identifier(cls, value):
        if isinstance(value, str) and (value != value.strip() or any(ord(c) < 32 for c in value)):
            raise ValueError("Invalid identifier.")
        return value


Identifier = Annotated[StrictStr, Field(min_length=1, max_length=80)]


class BetRequest(ActionRequest):
    matchId: Identifier
    teamId: Identifier
    amount: Annotated[StrictInt, Field(gt=0, le=MAX_POINTS)]


class CheerRequest(ActionRequest):
    seasonId: Identifier
    teamId: Identifier


class BetOut(BaseModel):
    id: int
    seasonId: str
    matchId: str
    teamId: str
    amount: int
    status: Literal["pending", "won", "lost", "refunded"]
    payout: int | None
    cheerAction: Literal["cheering", "betrayal"] | None
    createdAt: datetime
    currentPoints: int | None = None
    cheer: "CheerOut | None" = None


class BetPage(BaseModel):
    items: list[BetOut]
    total: int
    limit: int
    offset: int


class CheerOut(BaseModel):
    id: int
    seasonId: str
    teamId: str
    sealHeld: bool
    sealRevokedAt: datetime | None
    finalizedAt: datetime | None
    cheeringCount: int
    betrayalCount: int
    createdAt: datetime


class MatchContextOut(BaseModel):
    matchId: str
    seasonId: str
    currentPoints: int
    cheer: CheerOut | None
    bet: BetOut | None
    bettingOpen: bool
    eligible: bool
    reason: str | None
    sessionExpiresAt: datetime
    myRank: int | None = None


BetOut.model_rebuild()
