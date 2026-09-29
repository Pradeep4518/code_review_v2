from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .config import MAX_DIFF_CHARS

Category = Literal["Architecture", "Database", "Security", "Logging", "Testing", "API", "Performance", "Other"]
ReviewMode = Literal["general", "security", "architecture", "performance", "full"]
FeedbackType = Literal["accepted", "rejected", "incorrect", "helpful"]


class ReviewRequest(BaseModel):
    code_diff: str = Field(..., min_length=1, max_length=MAX_DIFF_CHARS)
    pr_title: str = Field("", max_length=200)
    bypass_memory: bool = False
    review_mode: ReviewMode = "full"

    @field_validator("code_diff")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("code_diff must not be blank")
        return v


class TeachRequest(BaseModel):
    rule: str = Field(..., min_length=8, max_length=1000)
    category: Category = "Other"
    source: str = Field("developer", max_length=80)
    owner: str = Field("", max_length=80)      # who taught it (knowledge stays after they leave)
    reason: str = Field("", max_length=500)    # why the rule exists

    @field_validator("rule")
    @classmethod
    def strip_rule(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 8:
            raise ValueError("rule is too short")
        return v


class FeedbackRequest(BaseModel):
    review_id: str = Field(..., min_length=1, max_length=64)
    issue_id: Optional[str] = Field(None, max_length=64)
    feedback_type: FeedbackType
    comment: str = Field("", max_length=1000)
    teach_as_rule: bool = False
    category: Optional[Category] = None


# ---------------------------------------------------------------- rule lifecycle (edit / retire / replace / delete)
class EditRuleRequest(BaseModel):
    rule: Optional[str] = Field(None, max_length=1000)
    category: Optional[Category] = None
    reason: Optional[str] = Field(None, max_length=500)
    actor: str = Field("", max_length=80)     # who is making the change

    @field_validator("rule")
    @classmethod
    def clean_rule(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = " ".join(v.split())
        if len(v) < 8:
            raise ValueError("rule is too short")
        return v


class RetireRequest(BaseModel):
    actor: str = Field("", max_length=80)
    reason: str = Field("", max_length=500)   # why it is outdated


class RestoreRequest(BaseModel):
    actor: str = Field("", max_length=80)


class SupersedeRequest(BaseModel):
    rule: Optional[str] = Field(None, max_length=1000)   # write a brand-new replacement rule ...
    new_rule_id: Optional[str] = Field(None, max_length=200)  # ... or point at a rule that already exists
    category: Optional[Category] = None
    reason: str = Field("", max_length=500)
    actor: str = Field("", max_length=80)

    @field_validator("rule")
    @classmethod
    def clean_rule(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = " ".join(v.split())
        if len(v) < 8:
            raise ValueError("rule is too short")
        return v
