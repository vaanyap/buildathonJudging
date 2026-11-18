from typing import Literal

from pydantic import BaseModel, Field, field_validator


class EvaluationIn(BaseModel):
    scores: dict[str, int | None] = Field(default_factory=dict)
    private_notes: str = ""
    formal_feedback: str = ""
    alignment: Literal["strong", "partial", "weak"] | None = None
    alignment_explanation: str = ""
    cross_track_flag: bool = False
    recommended_track_id: int | None = None
    cross_track_explanation: str = ""
    revision: int | None = None

    @field_validator("alignment", mode="before")
    @classmethod
    def blank_alignment(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("recommended_track_id", mode="before")
    @classmethod
    def blank_track(cls, value: object) -> object:
        if value == "" or value == 0:
            return None
        return value
