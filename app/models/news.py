"""News schemas: raw articles (instant) vs strict LLM insight (validated)."""
from typing import Literal

from pydantic import BaseModel, Field

Sentiment = Literal["positive", "neutral", "negative"]


class Article(BaseModel):
    title: str
    link: str
    published: str = ""
    source: str = ""


class NewsResponse(BaseModel):
    """Instant response: raw articles, no AI."""

    query: str
    articles: list[Article]


class NewsInsightOut(BaseModel):
    """STRICT validation target for Claude output."""

    model_config = {"extra": "forbid"}
    summary: str = Field(min_length=1)
    sentiment: Sentiment
    key_points: list[str] = []


class NewsInsight(BaseModel):
    summary: str = ""
    sentiment: Sentiment = "neutral"
    key_points: list[str] = []
    degraded: bool = False
