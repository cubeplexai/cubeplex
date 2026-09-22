"""Shared API responses for lifecycle cleanup requests."""

from pydantic import BaseModel


class AccessRemovalResponse(BaseModel):
    removed: bool
    cleanup_pending: bool


class LeaveWorkspaceResponse(BaseModel):
    left: bool
    cleanup_pending: bool


class HardDeleteResponse(BaseModel):
    deleted: bool
    cleanup_pending: bool
