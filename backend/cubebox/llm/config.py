"""LLM Configuration Models

Defines configuration for different LLM providers matching config.yaml structure.
"""

from typing import Any

from pydantic import BaseModel, Field


class ModelCost(BaseModel):
    """Cost configuration for a model"""

    currency: str = Field(default="USD", description="Currency code (ISO 4217)")
    input: float = Field(description="Input token cost per million tokens")
    output: float = Field(description="Output token cost per million tokens")
    cache_read: float = Field(
        default=0, description="Cache read cost per million tokens", alias="cache_read"
    )
    cache_write: float = Field(
        default=0,
        description="Cache write cost per million tokens",
        alias="cache_write",
    )

    class Config:
        populate_by_name = True


class ModelConfig(BaseModel):
    """Configuration for a specific model"""

    id: str = Field(description="Model identifier")
    name: str = Field(description="Model display name")
    reasoning: bool = Field(default=False, description="Whether this is a reasoning model")
    input: list[str] = Field(
        default=["text"], description="Supported input types (text, image, etc.)"
    )
    cost: ModelCost = Field(
        default_factory=lambda: ModelCost(input=0, output=0),
        description="Cost configuration",
    )
    context_window: int = Field(description="Context window size in tokens", alias="contextWindow")
    max_tokens: int = Field(description="Maximum output tokens", alias="maxTokens")
    extra_body: dict[str, Any] = Field(
        default_factory=dict, description="Extra body parameters", alias="extra_body"
    )
    extra_headers: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra headers",
        alias="extra_headers",
    )

    class Config:
        populate_by_name = True


class ProviderConfig(BaseModel):
    """Configuration for an LLM provider"""

    base_url: str = Field(description="Base URL for API", alias="base_url")
    api_key: str | None = Field(default=None, description="API key", alias="api_key")
    api: str = Field(
        default="openai-completions",
        description="API type (openai-completions, anthropic, etc.)",
    )
    extra_body: dict[str, Any] = Field(
        default_factory=dict, description="Extra body parameters", alias="extra_body"
    )
    extra_headers: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra headers",
        alias="extra_headers",
    )
    models: list[ModelConfig] = Field(default_factory=list, description="Available models")

    class Config:
        populate_by_name = True


class LLMConfig(BaseModel):
    """Root LLM configuration matching config.yaml structure"""

    default_model: str | None = Field(
        default=None,
        description="Default model in 'provider/model-id' format",
        alias="default_model",
    )
    fallback_models: list[str] = Field(
        default_factory=list,
        description="Fallback models in 'provider/model-id' format, tried in order",
        alias="fallback_models",
    )
    providers: dict[str, ProviderConfig] = Field(
        default_factory=dict, description="LLM providers configuration"
    )

    class Config:
        populate_by_name = True
