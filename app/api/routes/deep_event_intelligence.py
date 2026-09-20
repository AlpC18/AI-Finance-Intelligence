"""Omni-Source Geopolitical, Social Media & Deep Event Intelligence API endpoints."""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.routes.auth import get_current_user
from app.models.deep_event_intelligence import (
    GeopoliticalConflictImpact,
    OmniEventIntelligenceReport,
    SocialInfluencerImpact,
    SurpriseDeltaReport,
)
from app.models.user import User
from app.services.deep_event_intelligence_service import DeepEventIntelligenceService
from app.services.geopolitical_conflict_service import GeopoliticalConflictService
from app.services.social_influencer_service import SocialInfluencerService

router = APIRouter(prefix="/api/deep-event-intelligence", tags=["deep-event-intelligence"])


class OmniEventRequest(BaseModel):
    headline: str
    source_name: Optional[str] = "Reuters"
    handle: Optional[str] = None
    expected_metric: Optional[float] = None
    actual_metric: Optional[float] = None
    metric_name: Optional[str] = None


class SocialPostRequest(BaseModel):
    handle: str = "@elonmusk"
    post_text: str
    is_verified: Optional[bool] = True


class GeopoliticalRequest(BaseModel):
    headline: str
    content: Optional[str] = ""


@router.post("/analyze", response_model=OmniEventIntelligenceReport)
def analyze_omni_event(
    request: OmniEventRequest,
    current_user: User = Depends(get_current_user),
) -> OmniEventIntelligenceReport:
    """Analyze unified omni-source intelligence (credibility, social, war, ripple graph, options)."""
    svc = DeepEventIntelligenceService()
    return svc.analyze_omni_event(
        headline=request.headline,
        source_name=request.source_name or "Reuters",
        handle=request.handle,
        expected_metric=request.expected_metric,
        actual_metric=request.actual_metric,
        metric_name=request.metric_name,
    )


@router.post("/social/tweet", response_model=SocialInfluencerImpact)
def analyze_social_tweet(
    request: SocialPostRequest,
    current_user: User = Depends(get_current_user),
) -> SocialInfluencerImpact:
    """Analyze Twitter/X post impact (Elon Musk, Anthropic Claude, Nvidia, Sam Altman)."""
    svc = SocialInfluencerService()
    return svc.analyze_social_post(
        handle=request.handle,
        post_text=request.post_text,
        is_verified=request.is_verified if request.is_verified is not None else True,
    )


@router.post("/geopolitical/war-room", response_model=GeopoliticalConflictImpact)
def analyze_geopolitical_conflict(
    request: GeopoliticalRequest,
    current_user: User = Depends(get_current_user),
) -> GeopoliticalConflictImpact:
    """Analyze Middle East / Iran-Israel war tensions, oil routes, and defense stock surges."""
    svc = GeopoliticalConflictService()
    return svc.analyze_conflict_headline(headline=request.headline, raw_text=request.content or "")


@router.get("/surprise-calculator", response_model=SurpriseDeltaReport)
def calculate_surprise_delta(
    metric_name: str = Query(default="Quarterly EPS ($)"),
    expected: float = Query(default=1.20),
    actual: float = Query(default=1.45),
    current_user: User = Depends(get_current_user),
) -> SurpriseDeltaReport:
    """Calculate numerical surprise delta percentage between consensus expectation and print."""
    svc = DeepEventIntelligenceService()
    return svc.calculate_surprise_delta(metric_name=metric_name, expected_consensus=expected, actual_print=actual)
