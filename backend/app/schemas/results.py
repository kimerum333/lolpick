from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Result = Literal["team_a_win", "team_b_win", "draw", "cancelled"]


class MatchResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    result: Result
    score_a: int | None = Field(default=None, ge=0, le=32_767)
    score_b: int | None = Field(default=None, ge=0, le=32_767)

    @model_validator(mode="after")
    def consistent_scores(self):
        if (self.score_a is None) != (self.score_b is None):
            raise ValueError("Supply both scores or neither.")
        if self.score_a is not None:
            if self.result == "team_a_win" and self.score_a <= self.score_b:
                raise ValueError("The winning team's score must be greater.")
            if self.result == "team_b_win" and self.score_b <= self.score_a:
                raise ValueError("The winning team's score must be greater.")
            if self.result == "draw" and self.score_a != self.score_b:
                raise ValueError("A draw requires equal scores.")
            if self.result == "cancelled":
                raise ValueError("Cancellation must not include scores.")
        return self
