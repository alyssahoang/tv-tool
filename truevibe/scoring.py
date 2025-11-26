from __future__ import annotations

import re
from typing import Any, Dict, Optional, Set


def _clamp(score: float) -> float:
    return max(1.0, min(5.0, float(score)))


def _average(*values: Optional[float]) -> float:
    cleaned = [float(value) for value in values if value is not None]
    if not cleaned:
        return 0.0
    return sum(cleaned) / len(cleaned)


def compute_content_score(originality: float, creativity: float, balance: Optional[float] = None) -> float:
    """
    Content now uses two required prompts (Originality, Creative) and an optional Balance legacy field.
    """
    return round(_average(_clamp(originality), _clamp(creativity), _clamp(balance) if balance is not None else None), 2)


def compute_authority_score(*components: Optional[float]) -> float:
    """
    Authority collapses into a single slider but we keep support for multiple inputs if needed.
    """
    cleaned = [_clamp(value) for value in components if value is not None]
    if not cleaned:
        return 0.0
    return round(sum(cleaned) / len(cleaned), 2)


def compute_values_score(*components: Optional[float]) -> float:
    cleaned = [_clamp(value) for value in components if value is not None]
    if not cleaned:
        return 0.0
    return round(sum(cleaned) / len(cleaned), 2)


def _keyword_set(*parts: Optional[str]) -> Set[str]:
    tokens: Set[str] = set()
    for part in parts:
        if not part:
            continue
        for token in re.findall(r"[A-Za-z0-9]+", str(part).lower()):
            if len(token) >= 3:
                tokens.add(token)
    return tokens


def _collect_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(item) for item in value if item)
    return str(value)


def estimate_reach_score(follower_count: Optional[int]) -> float:
    if not follower_count or follower_count <= 0:
        return 1.0
    thresholds = [
        (10_000, 2.0),
        (50_000, 3.0),
        (200_000, 4.0),
        (500_000, 4.5),
        (1_000_000, 5.0),
    ]
    score = 1.5
    for boundary, boundary_score in thresholds:
        if follower_count >= boundary:
            score = boundary_score
    return round(min(score, 5.0), 2)


def estimate_interest_score(topic_text: Optional[str], objective_text: Optional[str]) -> float:
    topic_tokens = _keyword_set(topic_text)
    objective_tokens = _keyword_set(objective_text)
    if not topic_tokens or not objective_tokens:
        return 3.0
    overlap = len(topic_tokens & objective_tokens)
    if overlap >= 3:
        return 5.0
    if overlap == 2:
        return 4.0
    if overlap == 1:
        return 3.5
    return 2.5


def _parse_percentage(value: Any) -> Optional[float]:
    if not value:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", str(value))
    if not match:
        return None
    return float(match.group(1))


def extract_engagement_rate(details: Optional[Dict[str, Any]]) -> float:
    if not isinstance(details, dict):
        return 0.0
    for key in (
        "Instagram Engagement Rate",
        "TikTok Engagement Rate",
        "Engagement Rate",
    ):
        rate = _parse_percentage(details.get(key))
        if rate is not None:
            return rate
    return 0.0


def engagement_score_from_rate(rate_percent: float) -> float:
    if rate_percent >= 6.0:
        return 5.0
    if rate_percent >= 4.0:
        return 4.0
    if rate_percent >= 2.0:
        return 3.0
    if rate_percent >= 1.0:
        return 2.0
    if rate_percent > 0:
        return 1.5
    return 1.0


def derive_quantitative_scores(
    follower_count: Optional[int],
    demographics: Optional[Dict[str, Any]],
    campaign_objective: Optional[str],
) -> Dict[str, float]:
    demo = demographics or {}
    details = demo.get("details") if isinstance(demo, dict) else None
    topic_text = " ".join(
        filter(
            None,
            [
                _collect_text(demo.get("tags")),
                _collect_text(demo.get("categories")),
                _collect_text(demo.get("subCategories")),
                _collect_text((details or {}).get("Tags")),
                _collect_text((details or {}).get("Category")),
            ],
        )
    )
    interest = estimate_interest_score(topic_text, campaign_objective or (details or {}).get("About"))
    reach = estimate_reach_score(follower_count)
    engagement_rate = extract_engagement_rate(details)
    engagement_score = engagement_score_from_rate(engagement_rate)
    return {
        "reach_score": round(reach, 2),
        "interest_score": round(interest, 2),
        "engagement_rate": round(engagement_rate, 4),
        "engagement_score": round(engagement_score, 2),
    }


def compute_total_score(
    reach_score: float,
    interest_score: float,
    engagement_score: float,
    content_score: float,
    authority_score: float,
    values_score: float,
) -> float:
    total = (
        _clamp(reach_score)
        + _clamp(interest_score)
        + _clamp(engagement_score)
        + content_score
        + authority_score
        + values_score
    )
    return round(total, 2)


def build_score_payload(
    *,
    reach_score: float,
    interest_score: float,
    engagement_rate: float,
    engagement_score: float,
    content_originality: float,
    content_creativity: float,
    authority_overall: float,
    values_overall: float,
    qualitative_notes: str,
) -> Dict[str, float | str]:
    content_score = compute_content_score(content_originality, content_creativity)
    authority_score = compute_authority_score(authority_overall)
    values_score = compute_values_score(values_overall)
    total_score = compute_total_score(
        reach_score,
        interest_score,
        engagement_score,
        content_score,
        authority_score,
        values_score,
    )
    return {
        "reach_score": round(_clamp(reach_score), 2),
        "interest_score": round(_clamp(interest_score), 2),
        "engagement_rate": round(float(engagement_rate or 0.0), 4),
        "engagement_score": round(_clamp(engagement_score), 2),
        "content_originality": round(_clamp(content_originality), 2),
        "content_creativity": round(_clamp(content_creativity), 2),
        "content_score": content_score,
        "authority_score": authority_score,
        "values_score": values_score,
        "total_score": total_score,
        "qualitative_notes": qualitative_notes.strip(),
    }
