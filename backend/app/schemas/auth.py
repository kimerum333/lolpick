from uuid import UUID
from datetime import datetime
from pydantic import BaseModel


class MemberOut(BaseModel):
    memberId: UUID
    currentPoints: int
    currentSeasonId: str | None
    availableSeasonIds: list[str] = []
    sessionExpiresAt: datetime | None = None
