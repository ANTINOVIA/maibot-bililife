from datetime import datetime
from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


TaskType = Literal["discover", "watch", "explore", "read_comments", "draft", "replies", "memory", "review", "rest", "account", "login", "publish", "reconcile", "plan"]


class ProposedTask(Strict):
    type: Literal["discover", "explore", "read_comments", "draft", "replies", "memory", "review", "rest"]
    earliest_start: str
    deadline: str
    goal: str = Field(min_length=1, max_length=200)
    query: str = Field(default="", max_length=80)
    max_items: int = Field(default=1, ge=1, le=4)
    max_duration_seconds: int = Field(default=240, ge=10, le=600)
    depends_on: List[int] = Field(default_factory=list, max_length=8)
    reason: str = Field(max_length=300)

    @model_validator(mode="after")
    def times(self):
        start, end = datetime.fromisoformat(self.earliest_start), datetime.fromisoformat(self.deadline)
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("任务时间须含时区且截止晚于开始")
        return self


class PlanProposal(Strict):
    theme: str = Field(min_length=1, max_length=120)
    tasks: List[ProposedTask] = Field(min_length=1, max_length=10)


class DiscoveryChoice(Strict):
    query: str = Field(min_length=1, max_length=80)
    reason: str = Field(max_length=300)


class ScheduleAdjustment(Strict):
    reason: str = Field(max_length=300)
    tasks: List[ProposedTask] = Field(default_factory=list, max_length=2)


class Selection(Strict):
    browse: bool
    reason: str = Field(max_length=300)
    topic: str = Field(max_length=80)


class CommentPoint(Strict):
    rpid: str = Field(max_length=30)
    quote: str = Field(min_length=1,max_length=300)
    interpretation: str = Field(max_length=300)


class CommentUnderstanding(Strict):
    summary: str = Field(max_length=1200)
    points: List[CommentPoint] = Field(max_length=8)
    feeling: str = Field(max_length=300)
    questions: List[str] = Field(max_length=5)
    uncertainty: str = Field(max_length=400)


class Detail(Strict):
    claim_type: Literal["author_claim", "checkable_detail", "personal_opinion"]
    text: str = Field(max_length=250)
    evidence_quote: str = Field(max_length=250)


class Understanding(Strict):
    summary: str = Field(max_length=1600)
    details: List[Detail] = Field(max_length=8)
    feeling: str = Field(max_length=300)
    questions: List[str] = Field(max_length=5)
    uncertainty: str = Field(max_length=400)
    visual_required: bool
    suitable_for_comment: bool
    reason: str = Field(max_length=300)


class InteractionDecision(Strict):
    interact: bool
    reason: str = Field(max_length=300)
    evidence_quote: str = Field(default="", max_length=300)


class Draft(Strict):
    text: str = Field(max_length=350)
    evidence_quote: str = Field(max_length=300)
    reason: str = Field(max_length=300)


class MemoryProposal(Strict):
    text: str = Field(max_length=1200)
    importance: float = Field(ge=0, le=1)
    topics: List[str] = Field(max_length=8)


class InterestDelta(Strict):
    topic: str = Field(min_length=1, max_length=80)
    delta: float = Field(ge=-0.05, le=0.05)
    reason: str = Field(max_length=300)


class Evolution(Strict):
    changes: List[InterestDelta] = Field(max_length=4)


class Review(Strict):
    summary: str = Field(max_length=2000)
    learned: List[str] = Field(max_length=8)
    unresolved: List[str] = Field(max_length=8)
    tomorrow: List[str] = Field(max_length=6)
