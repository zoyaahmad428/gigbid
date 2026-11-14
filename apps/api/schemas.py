"""Typed request and response bodies for the API (the graph's own hand-offs live in agent/handoffs.py)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Persona = Literal["ai_ml", "fullstack", "data_analyst"]


class Credentials(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    password: str = Field(min_length=8, max_length=200)


class Session(BaseModel):
    token: str
    user: dict


class Settings(BaseModel):
    """What onboarding collects and the settings screen edits. The agents read these, not files."""
    persona: Persona
    name: str = Field(min_length=1, max_length=120)
    headline: str = Field("", max_length=300)
    hourly_rate: float = Field(gt=0, le=1000)
    min_fixed_budget: float = Field(ge=0, le=100000)
    marketplace_token: str = Field(min_length=1, max_length=200)
    autonomy_auto_skip: bool = True
    timezone: str = "Asia/Karachi"
    weekly_connects_budget: int = Field(100, ge=0, le=1000)


class StartRun(BaseModel):
    force_bid: bool = False


class Decision(BaseModel):
    """Mirrors agent.handoffs.ApprovalDecision; validated against it before the graph resumes."""
    action: Literal["approve", "edit", "reject"]
    edited_text: str | None = Field(None, max_length=20000)
    edited_bid: float | None = Field(None, gt=0)
    reason: str | None = Field(None, max_length=1000)


class RunRef(BaseModel):
    thread_id: str
    status: str
