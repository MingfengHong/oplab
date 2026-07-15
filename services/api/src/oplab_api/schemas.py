from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class CreateProjectRequest(BaseModel):
    title: str = Field(min_length=2, max_length=240)
    question: str = Field(min_length=10, max_length=4_000)
    success_criteria: list[str] = Field(default_factory=list, max_length=20)


class AddUrlRequest(BaseModel):
    url: HttpUrl


class MeetingDecisionRequest(BaseModel):
    kind: Literal["continue", "revise", "stop"]
    rationale: str = Field(min_length=3, max_length=4_000)
    direction: str | None = Field(default=None, max_length=4_000)
    dissent: list[str] = Field(default_factory=list, max_length=20)
