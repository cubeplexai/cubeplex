"""Request/response models for conversational skill discovery + install."""

from __future__ import annotations

from pydantic import BaseModel


class SkillCandidateResponse(BaseModel):
    candidate_id: str
    name: str
    canonical_name: str
    description: str
    source_kind: str
    keywords: list[str]
    version: str | None
    trust: str
    install_state: str
    stars: int | None = None
    install_count: int | None = None
    source_name: str
    repo: str | None = None
    unvetted: bool


class CandidatePreviewResponse(BaseModel):
    candidate_id: str
    name: str
    canonical_name: str
    content: str
    env_vars: list[str] = []


class InstallCandidateRequest(BaseModel):
    candidate_id: str


class AdminInstallCandidateRequest(BaseModel):
    candidate_id: str


class InstallCandidateResponse(BaseModel):
    canonical_name: str
    skill_id: str
    installed_version: str


class SkillRefreshResponse(BaseModel):
    canonical_name: str
    skill_id: str
    installed_version: str
    changed: bool
