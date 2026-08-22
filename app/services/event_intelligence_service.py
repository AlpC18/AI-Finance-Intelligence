"""Event-driven intelligence: ingest -> classify -> impact-score -> block.

Classification and scoring are pure, keyword-driven functions run in a threadpool
so text processing never blocks the event loop. Feed failures degrade to an empty
event set rather than raising.
"""
from __future__ import annotations

from typing import Optional, Protocol

from starlette.concurrency import run_in_threadpool

from app.models.event import EventCategory, MarketEvent

_LEGAL = ("sec", "lawsuit", "court", "docket", "subpoena", "settlement", "charges",
          "sued", "litigation", "indictment", "regulator", "ban", "fraud", "probe",
          "hearing", "verdict", "ruling")
_MACRO = ("fed", "fomc", "interest rate", "rate hike", "rate cut", "cpi", "inflation",
          "jobs report", "ecb", "central bank", "monetary policy", "gdp",
          "unemployment", "payrolls", "pce", "rate decision")
_TECH = ("launch", "unveils", "chip", "ai model", "partnership", "acquisition",
         "acquire", "merger", "earnings beat", "breakthrough", "patent", "upgrade",
         "approval", "product")
_EXEC = ("ceo", "musk", "tweet", "tweeted", "posted", "said", "warns", "comments",
         "hints", "elon", "powell", "statement")
_NOISE = ("you won't believe", "shocking", "top 10", "click here", "one weird trick",
          "must see", "goes viral", "clickbait", "you need to know")
_BEARISH = ("lawsuit", "ban", "hike", "crash", "sell-off", "selloff", "downgrade",
            "miss", "fraud", "charges", "probe", "warns", "plunge", "halt", "subpoena")
_BULLISH = ("beat", "launch", "cut", "upgrade", "partnership", "approval", "acquire",
            "record", "surge", "rally", "breakthrough")
_VOL = ("fomc", "cpi", "decision", "hearing", "verdict", "ruling", "announcement",
        "rate decision", "testimony")
_URGENT = ("today", "within", "hours", "imminent", "breaking", " now", "this week")
_BASE = {"LEGAL_RISK": 6, "MACRO_POLICY": 6, "TECH_CATALYST": 4, "EXECUTIVE_SENTIMENT": 3}


class _Provider(Protocol):
    async def fetch_raw(self, limit: int = 40) -> list[dict]: ...


def _count(text: str, words: tuple[str, ...]) -> int:
    return sum(1 for w in words if w in text)


def _matches_any(text: str) -> bool:
    return bool(_count(text, _LEGAL) or _count(text, _MACRO)
                or _count(text, _TECH) or _count(text, _EXEC))


def classify_text(text: str) -> Optional[EventCategory]:
    """Categorize text; None means noise/non-actionable and should be discarded."""
    t = text.lower()
    if not t.strip():
        return None
    if any(n in t for n in _NOISE) and not _matches_any(t):
        return None
    scores: dict[EventCategory, int] = {
        "LEGAL_RISK": _count(t, _LEGAL),
        "MACRO_POLICY": _count(t, _MACRO),
        "TECH_CATALYST": _count(t, _TECH),
        "EXECUTIVE_SENTIMENT": _count(t, _EXEC),
    }
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else None


def score_event(text: str, category: EventCategory) -> tuple[int, str]:
    """Return (severity 1-10, orientation) for a categorized event."""
    t = text.lower()
    base = _BASE[category]
    if any(u in t for u in _URGENT):
        base += 2
    if _count(t, _VOL):
        base += 1
    severity = max(1, min(base, 10))

    if _count(t, _VOL) and category in ("MACRO_POLICY", "LEGAL_RISK"):
        orientation = "HIGH_VOLATILITY"
    elif _count(t, _BEARISH) > _count(t, _BULLISH):
        orientation = "BEARISH"
    elif _count(t, _BULLISH) > 0:
        orientation = "BULLISH"
    else:
        orientation = "NEUTRAL"
    return severity, orientation


def _to_event(item: dict) -> Optional[MarketEvent]:
    text = f"{item.get('title', '')} {item.get('summary', '')}".strip()
    category = classify_text(text)
    if category is None:
        return None
    severity, orientation = score_event(text, category)
    blocking = (
        severity >= 8
        and category in ("LEGAL_RISK", "MACRO_POLICY")
        and orientation in ("BEARISH", "HIGH_VOLATILITY")
    )
    return MarketEvent(
        title=(item.get("title", "") or "")[:280],
        source=item.get("source", ""),
        link=item.get("link", ""),
        published=item.get("published", ""),
        category=category,
        severity=severity,
        orientation=orientation,
        blocking=blocking,
    )


def process_batch(raw: list[dict]) -> list[MarketEvent]:
    events = [e for e in (_to_event(it) for it in raw) if e is not None]
    events.sort(key=lambda e: e.severity, reverse=True)
    return events


class EventIntelligenceService:
    def __init__(self, provider: _Provider) -> None:
        self._provider = provider

    async def get_events(self, limit: int = 40) -> list[MarketEvent]:
        try:
            raw = await self._provider.fetch_raw(limit)
        except Exception:  # noqa: BLE001 - feed outage degrades to no events
            return []
        return await run_in_threadpool(process_batch, raw)

    async def high_severity(self, threshold: int = 8) -> list[MarketEvent]:
        return [e for e in await self.get_events() if e.severity >= threshold]

    async def ai_context(self, top: int = 8) -> tuple[list[dict], Optional[str]]:
        """Compact event payload + a blocking directive string (or None) for the LLM."""
        events = await self.get_events()
        payload = [
            {"category": e.category, "severity": e.severity,
             "orientation": e.orientation, "title": e.title}
            for e in events[:top]
        ]
        blocking = None
        for e in events:
            if e.blocking:
                blocking = (
                    f"DO NOT TRADE: {e.category} ({e.orientation}, severity "
                    f"{e.severity}) — {e.title}"
                )
                break
        return payload, blocking
