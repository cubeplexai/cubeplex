"""Pydantic schemas for workspace settings endpoints."""

from pydantic import BaseModel, Field


class AgentConfigOut(BaseModel):
    system_prompt: str


class AgentConfigPatch(BaseModel):
    system_prompt: str = Field(max_length=8000)
