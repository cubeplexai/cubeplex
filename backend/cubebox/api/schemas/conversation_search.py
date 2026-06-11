"""Pydantic schemas for the conversation search route."""

from pydantic import BaseModel, Field


class SearchResultSchema(BaseModel):
    conversation_id: str
    title: str
    snippet: str
    match_offsets: list[tuple[int, int]] = Field(default_factory=list)
    matched_message_seq: int | None = None
    matched_at: str | None = None
    score: float


class SearchResponseSchema(BaseModel):
    results: list[SearchResultSchema]
    lexical_count: int
    vector_count: int
    fused_count: int
