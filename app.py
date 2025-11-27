from __future__ import annotations

import json
import sqlite3
import base64
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from html import escape
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar
from urllib.parse import quote_plus, urlparse

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from truevibe import auth, database, ingestion, scoring, scraping
from truevibe.creatoriq import CreatorIQError, is_creatoriq_link

T = TypeVar("T")
NAV_SECTIONS: Dict[str, str] = {
    "Campaign Briefs": "Briefs & context",
    "Score Influencers": "Import & Evaluate",
    "Insights & Reports": "Insights",
}
PLATFORM_PROFILE_URLS: Dict[str, str] = {
    "instagram": "https://www.instagram.com/{handle}",
    "tiktok": "https://www.tiktok.com/@{handle}",
    "youtube": "https://www.youtube.com/@{handle}",
    "facebook": "https://www.facebook.com/{handle}",
    "x": "https://x.com/{handle}",
}
VERO_COLORWAY = ["#0A6CC2", "#4BB7E5", "#0A223A", "#F6C343", "#F48668"]
LINK_ICON_MAP: Dict[str, Dict[str, str]] = {
    "instagram": {"icon": "", "label": "Instagram", "color": "#E4405F"},
    "tiktok": {"icon": "", "label": "TikTok", "color": "#010101"},
    "youtube": {"icon": "", "label": "YouTube", "color": "#FF0000"},
    "facebook": {"icon": "", "label": "Facebook", "color": "#1778F2"},
    "fb.com": {"icon": "", "label": "Facebook", "color": "#1778F2"},
    "x.com": {"icon": "", "label": "X / Twitter", "color": "#111827"},
    "twitter": {"icon": "", "label": "X / Twitter", "color": "#111827"},
    "creatoriq": {"icon": "", "label": "CreatorIQ", "color": "#0A6CC2"},
}
ICON_FILES: Dict[str, Path] = {
    "instagram": Path("app/img/icon-instagram.png"),
    "tiktok": Path("app/img/icon-tiktok.webp"),
    "facebook": Path("app/img/icon-facebook.png"),
    "youtube": Path("app/img/icon-youtube.png"),
    "x": Path("app/img/icon-x.png"),
}
ICON_CACHE: Dict[str, tuple[str, str]] = {}


def _parse_compact_number(raw: str) -> Optional[float]:
    cleaned = raw.replace(",", "").strip().upper()
    match = re.match(r"(-?\d+(?:\.\d+)?)([KMB]?)$", cleaned)
    if not match:
        try:
            return float(cleaned)
        except ValueError:
            return None
    num = float(match.group(1))
    suffix = match.group(2)
    multiplier = 1.0
    if suffix == "K":
        multiplier = 1_000.0
    elif suffix == "M":
        multiplier = 1_000_000.0
    elif suffix == "B":
        multiplier = 1_000_000_000.0
    return num * multiplier


def _parse_percentage_value(raw: str) -> Optional[float]:
    text = raw.replace("%", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _normalize_detail_entry(detail_key: str, value: Any) -> tuple[str, Any]:
    if not isinstance(value, str):
        return f"Detail - {detail_key}", value
    text = value.strip()
    lowered_value = text.lower()
    lowered_key = detail_key.lower()

    def _trim_suffix(suffix: str) -> None:
        nonlocal text, lowered_value
        if lowered_value.endswith(suffix):
            text = text[: -len(suffix)].strip()
            lowered_value = text.lower()

    _trim_suffix(" followers")
    _trim_suffix(" engagement rate")
    column_key = detail_key
    if column_key.startswith("Instagram"):
        column_key = column_key.replace("Instagram", "").strip() or "Instagram"
    if column_key.startswith("TikTok"):
        column_key = column_key.replace("TikTok", "").strip() or "TikTok"

    if "followers" in lowered_key and ("%" in lowered_value or "engagement" in lowered_value):
        column_key = column_key.replace("Followers", "Engagement Rate") or "Engagement Rate"
        lowered_key = column_key.lower()
    elif "engagement rate" in lowered_key and ("followers" in lowered_value and "%" not in lowered_value):
        column_key = column_key.replace("Engagement Rate", "Followers") or "Followers"
        lowered_key = column_key.lower()

    numeric_value: Optional[float] = None
    if "followers" in lowered_key:
        numeric_value = _parse_compact_number(text)
    elif "engagement rate" in lowered_key:
        numeric_value = _parse_percentage_value(text)

    normalized_value: Any
    if numeric_value is not None:
        normalized_value = numeric_value
    else:
        normalized_value = text

    return f"Detail - {column_key}", normalized_value


def _compute_saturation_rate(organic_posts: float, sponsored_posts: float) -> Optional[float]:
    if sponsored_posts is None or sponsored_posts <= 0:
        return None
    if organic_posts is None or organic_posts < 0:
        organic_posts = 0.0
    return organic_posts / sponsored_posts


def _content_balance_score_from_rate(rate: Optional[float]) -> Optional[float]:
    if rate is None:
        return None
    if rate >= 0.7:
        return 1.0
    if rate >= 0.5:
        return 2.0
    if rate >= 0.4:
        return 3.0
    if rate >= 0.3:
        return 4.0
    if rate >= 0.2:
        return 5.0
    return None


def _safe_float(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _flatten_details(details: Any) -> Dict[str, Any]:
    if not isinstance(details, dict):
        return {"Detail - raw": details}
    flattened: Dict[str, Any] = {}
    for key, value in details.items():
        if isinstance(value, (dict, list)):
            column_name = f"Detail - {key}"
            flattened[column_name] = json.dumps(value)
        else:
            column_name, normalized = _normalize_detail_entry(key, value)
            flattened[column_name] = normalized
    return flattened


def _derive_quant_scores(
    follower_count: Optional[int],
    demographics: Optional[Dict[str, Any]],
    campaign_objective: Optional[str],
    fallback_row: Dict[str, Any],
) -> Dict[str, float]:
    if hasattr(scoring, "derive_quantitative_scores"):
        return scoring.derive_quantitative_scores(follower_count, demographics, campaign_objective)
    # Fallback for environments that still have the previous scoring module
    reach = float(fallback_row.get("reach_score") or 3.0)
    interest = float(fallback_row.get("interest_score") or 3.0)
    engagement_rate = float(fallback_row.get("engagement_rate") or 0.0)
    engagement_score = float(fallback_row.get("engagement_score") or 3.0)
    return {
        "reach_score": reach,
        "interest_score": interest,
        "engagement_rate": engagement_rate,
        "engagement_score": engagement_score,
    }


def init_session_state() -> None:
    if "user" not in st.session_state:
        st.session_state.user = None
    if "active_campaign_id" not in st.session_state:
        st.session_state.active_campaign_id = None
    if "active_view" not in st.session_state:
        st.session_state.active_view = "Campaign Briefs"


def inject_styles() -> None:
    """Inject custom CSS to align the UI with the premium Vero aesthetic."""
    st.markdown(
        """
        <style>
        :root {
            --tv-primary: #0A6CC2;
            --tv-secondary: #4BB7E5;
            --tv-bg: #F4F6FB;
            --tv-card-bg: rgba(255, 255, 255, 0.98);
            --tv-text: #0A223A;
            --tv-muted: #6B7280;
            --tv-border: rgba(217, 222, 231, 0.9);
        }
        html, body, [data-testid="stAppViewContainer"] {
            background: radial-gradient(circle at 35% 20%, #FFFFFF 0%, #E8ECF2 60%) fixed;
            color: var(--tv-text);
            font-family: "TT Commons Pro", "TT Commons", "Inter", "Poppins", sans-serif;
        }
        [data-testid="stHeader"] { background: transparent; }
        [data-testid="stSidebar"] {
            background: rgba(255, 255, 255, 0.97);
            border-right: 1px solid rgba(10, 34, 58, 0.08);
        }
        .block-container {
            padding: 2rem 4rem 4rem;
            color: var(--tv-text);
        }
        @media (max-width: 768px) {
            .block-container {
                padding: 1.5rem 1.5rem 3rem;
            }
        }
        .tv-hero {
            position: relative;
            background: linear-gradient(135deg, rgba(255,255,255,0.9), rgba(236,245,255,0.82));
            border: 1px solid rgba(10, 34, 58, 0.08);
            border-radius: 32px;
            padding: 2.5rem;
            box-shadow: 0 30px 55px rgba(10, 34, 58, 0.15);
            margin-bottom: 1.75rem;
            color: var(--tv-text);
            overflow: hidden;
        }
        .tv-hero::before,
        .tv-hero::after {
            content: "";
            position: absolute;
            inset: 0;
            border-radius: inherit;
            pointer-events: none;
        }
        .tv-hero::before {
            background: linear-gradient(120deg, rgba(10,108,194,0.05), rgba(75,183,229,0.18));
            opacity: 1;
        }
        .tv-hero::after {
            display: none;
        }
        .tv-hero h1 {
            font-size: 2.5rem;
            margin-bottom: 0.25rem;
        }
        .tv-hero p {
            color: rgba(10, 34, 58, 0.75);
            font-size: 1rem;
            max-width: 640px;
        }
        .tv-pill {
            display: inline-flex;
            padding: 0.25rem 1rem;
            border-radius: 999px;
            background: rgba(10, 108, 194, 0.1);
            color: var(--tv-primary);
            font-weight: 600;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            font-size: 0.75rem;
            margin-bottom: 0.65rem;
        }
        .tv-pill.small {
            padding: 0.15rem 0.75rem;
            font-size: 0.7rem;
            margin-bottom: 0.5rem;
        }
        .tv-status-chip {
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
            background: rgba(75, 183, 229, 0.14);
            border: 1px solid rgba(75, 183, 229, 0.35);
            color: var(--tv-text);
            padding: 0.35rem 0.9rem;
            border-radius: 999px;
            font-size: 0.9rem;
        }
        [data-testid="stVerticalBlockBorderWrapper"] {
            background: transparent;
            border: none;
            padding: 0;
            margin-bottom: 1.5rem;
        }
        [data-testid="stVerticalBlockBorderWrapper"] > div {
            position: relative;
            background: linear-gradient(135deg, rgba(255,255,255,0.98), rgba(240,248,255,0.82));
            border-radius: 30px;
            padding: 1.7rem;
            box-shadow: 0 35px 80px rgba(10, 34, 58, 0.18);
            overflow: hidden;
            backdrop-filter: blur(18px);
        }
        [data-testid="stVerticalBlockBorderWrapper"] > div::before {
            content: "";
            position: absolute;
            inset: 1px;
            border-radius: 29px;
            padding: 1px;
            background: linear-gradient(120deg, rgba(10,108,194,0.25), rgba(75,183,229,0.1));
            z-index: 0;
        }
        [data-testid="stVerticalBlockBorderWrapper"] > div::after {
            content: "";
            position: absolute;
            inset: 0;
            border-radius: inherit;
            background: radial-gradient(circle at top right, rgba(10,108,194,0.15), transparent 55%);
            opacity: 0.35;
            z-index: 0;
        }
        [data-testid="stVerticalBlockBorderWrapper"] > div > :not(style) {
            position: relative;
            z-index: 1;
        }
        .tv-card-title {
            margin: 0;
            font-size: 1.1rem;
            color: var(--tv-text);
        }
        .tv-card-subtitle {
            margin: 0.25rem 0 0.75rem;
            color: rgba(10, 34, 58, 0.6);
        }
        .tv-section-title {
            font-size: 1.25rem;
            margin-bottom: 1rem;
        }
        .stTabs [role="tablist"] {
            border: 1px solid rgba(10, 34, 58, 0.06);
            background: rgba(255, 255, 255, 0.95);
            border-radius: 999px;
            padding: 0.25rem;
            gap: 0.3rem;
        }
        .stTabs [role="tab"] {
            border-radius: 999px;
            padding: 0.4rem 1.25rem;
            color: rgba(10, 34, 58, 0.5);
            border: none;
        }
        .stTabs [aria-selected="true"] {
            background: linear-gradient(120deg, #0A6CC2, #4BB7E5);
            color: #FFFFFF;
            font-weight: 600;
        }
        .stButton>button, .stDownloadButton>button {
            background: linear-gradient(120deg, #0A6CC2, #4BB7E5);
            color: #FFFFFF;
            border: none;
            border-radius: 999px;
            padding: 0.6rem 1.5rem;
            font-weight: 600;
            box-shadow: 0 12px 26px rgba(10, 108, 194, 0.25);
        }
        .stButton>button:hover, .stDownloadButton>button:hover {
            box-shadow: 0 20px 35px rgba(10, 108, 194, 0.35);
        }
        button[id^="button-logout_btn"] {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.45rem;
            border-radius: 999px !important;
            border: 1px solid rgba(10, 108, 194, 0.45) !important;
            padding: 0.55rem 1.6rem !important;
            background: linear-gradient(135deg, rgba(255,255,255,0.82), rgba(236,245,255,0.65)) padding-box;
            color: var(--tv-text) !important;
            font-weight: 600 !important;
            letter-spacing: 0.05em;
            font-size: 0.82rem;
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.6), 0 18px 35px rgba(10, 34, 58, 0.18);
            position: relative;
            overflow: hidden;
            transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease, background 0.2s ease;
        }
        button[id^="button-logout_btn"]::after {
            content: "\\2197";
            font-size: 0.95rem;
            opacity: 0.65;
            transition: transform 0.2s ease, opacity 0.2s ease;
        }
        button[id^="button-logout_btn"]:hover {
            border-color: rgba(10, 108, 194, 0.75) !important;
            background: linear-gradient(120deg, rgba(10,108,194,0.08), rgba(75,183,229,0.35)) padding-box;
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.7), 0 26px 46px rgba(10, 108, 194, 0.35);
            color: var(--tv-primary) !important;
        }
        button[id^="button-logout_btn"]:hover::after {
            opacity: 1;
            transform: translateX(3px);
        }
        div[data-testid="stMetricValue"] {
            color: var(--tv-text);
        }
        div[data-testid="stMetricLabel"] {
            color: rgba(10, 34, 58, 0.6);
        }
        .stTable, .stDataFrame {
            background: rgba(255, 255, 255, 0.88);
            border-radius: 16px;
            color: var(--tv-text);
            border: 1px solid rgba(217, 222, 231, 0.6);
        }
        .tv-info-pill {
            background: rgba(10, 108, 194, 0.08);
            border-radius: 14px;
            border: 1px solid rgba(10, 108, 194, 0.15);
            padding: 0.65rem 1rem;
            display: flex;
            flex-direction: column;
            gap: 0.1rem;
            margin-bottom: 0.75rem;
        }
        .tv-info-pill span {
            font-size: 0.8rem;
            color: rgba(10, 34, 58, 0.7);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-weight: 600;
        }
        .tv-info-pill strong {
            font-size: 1.15rem;
            color: var(--tv-text);
        }
        .tv-campaign-summary {
            margin-top: 0.75rem;
            padding: 1.35rem 1.5rem;
            border-radius: 26px;
            border: 1px solid rgba(10, 34, 58, 0.12);
            background: linear-gradient(135deg, rgba(255,255,255,0.98), rgba(234,244,255,0.9));
            box-shadow: 0 28px 55px rgba(10, 34, 58, 0.16);
        }
        .tv-campaign-summary__header {
            display: flex;
            flex-direction: column;
            gap: 0.6rem;
        }
        .tv-campaign-eyebrow {
            margin: 0;
            font-size: 0.75rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: rgba(10, 34, 58, 0.55);
            font-weight: 600;
        }
        .tv-campaign-summary__header h4 {
            margin: 0.15rem 0 0.35rem;
            font-size: 1.35rem;
            color: var(--tv-text);
        }
        .tv-campaign-tags {
            display: flex;
            flex-wrap: wrap;
            gap: 0.35rem;
        }
        .tv-campaign-tag {
            display: inline-flex;
            align-items: center;
            padding: 0.2rem 0.75rem;
            border-radius: 999px;
            background: rgba(10, 108, 194, 0.08);
            border: 1px solid rgba(10, 108, 194, 0.15);
            font-size: 0.78rem;
            font-weight: 600;
            color: rgba(10, 34, 58, 0.75);
        }
        .tv-campaign-objective {
            margin-top: 1.2rem;
            padding: 1rem 1.15rem;
            border-radius: 20px;
            border: 1px solid rgba(10, 34, 58, 0.08);
            background: rgba(10, 108, 194, 0.04);
        }
        .tv-campaign-objective span {
            display: block;
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 0.08em;
            color: rgba(10, 34, 58, 0.6);
            font-weight: 600;
        }
        .tv-campaign-objective p {
            margin: 0.35rem 0 0;
            color: rgba(10, 34, 58, 0.85);
            font-size: 0.96rem;
            line-height: 1.5;
        }
        .tv-campaign-stats {
            margin-top: 1.1rem;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 0.75rem;
        }
        .tv-campaign-stat {
            padding: 0.75rem 0.85rem;
            border-radius: 16px;
            border: 1px solid rgba(10, 108, 194, 0.12);
            background: rgba(255, 255, 255, 0.9);
            box-shadow: 0 15px 25px rgba(10, 34, 58, 0.08);
        }
        .tv-campaign-stat label {
            display: block;
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: rgba(10, 34, 58, 0.55);
            margin-bottom: 0.2rem;
        }
        .tv-campaign-stat strong {
            font-size: 1.35rem;
            color: var(--tv-text);
        }
        .tv-campaign-meta-grid {
            margin-top: 1.2rem;
            padding-top: 0.9rem;
            border-top: 1px dashed rgba(10, 34, 58, 0.2);
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 0.8rem;
        }
        .tv-campaign-meta-grid span {
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-size: 0.72rem;
            color: rgba(10, 34, 58, 0.55);
        }
        .tv-campaign-meta-grid p {
            margin: 0.25rem 0 0;
            font-weight: 600;
            color: var(--tv-text);
        }
        .tv-link-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.4rem;
            margin-top: 0.35rem;
        }
        @media (max-width: 800px) {
            .tv-link-grid {
                grid-template-columns: repeat(auto-fit, minmax(90px, 1fr));
            }
        }
        .tv-link-badge {
            display: inline-flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 0.3rem;
            padding: 0.35rem 0;
            font-size: 0.78rem;
            text-decoration: none;
            color: var(--tv-primary);
            font-weight: 600;
        }
        .tv-link-badge img {
            width: 20px;
            height: 20px;
        }
        .tv-link-badge .tv-link-label {
            font-size: 0.72rem;
            line-height: 1.1;
        }
        .tv-link-badge .icon {
            font-size: 1rem;
        }
        .tv-select-hint {
            background: rgba(10, 108, 194, 0.08);
            border: 1px dashed rgba(10, 108, 194, 0.3);
            border-radius: 12px;
            padding: 0.35rem 0.75rem;
            font-size: 0.85rem;
            color: rgba(10, 34, 58, 0.75);
            margin-bottom: 0.4rem;
        }
        .tv-star-block {
            margin-top: 0.5rem;
        }
        .tv-star-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.5rem;
        }
        .tv-star-label {
            font-weight: 600;
            color: var(--tv-text);
            text-transform: uppercase;
            font-size: 0.9rem;
        }
        .tv-star-score {
            font-weight: 600;
            color: var(--tv-text);
            font-size: 0.9rem;
        }
        .tv-star-icons {
            display: flex;
            gap: 0.15rem;
            font-size: 1.1rem;
            margin-top: 0.25rem;
        }
        .tv-star {
            color: #F6C343;
        }
        .tv-star.empty {
            color: rgba(10, 34, 58, 0.2);
        }
        .tv-star.half {
            background: linear-gradient(90deg, #F6C343 50%, rgba(10, 34, 58, 0.2) 50%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            color: transparent;
        }
        input[type="text"], input[type="password"], textarea, select, .stSelectbox, .stNumberInput input {
            color: var(--tv-text) !important;
            background: #FFFFFF !important;
            border: 1px solid rgba(10, 34, 58, 0.2) !important;
            border-radius: 12px !important;
            padding: 0.55rem 0.9rem !important;
            font-size: 0.95rem !important;
            box-shadow: inset 0 1px 2px rgba(10, 34, 58, 0.08);
        }
        [data-baseweb="select"] > div {
            border-radius: 12px !important;
            border: 1px solid rgba(10, 34, 58, 0.2) !important;
            background: #FFFFFF !important;
            font-size: 0.95rem !important;
            color: var(--tv-text) !important;
            padding: 0.2rem 0.3rem;
        }
        [data-baseweb="select"] svg {
            color: var(--tv-primary) !important;
        }
        [data-baseweb="popover"] {
            border: 1px solid rgba(10, 34, 58, 0.15) !important;
            border-radius: 12px !important;
        }
        [data-baseweb="option"] {
            font-size: 0.9rem !important;
            padding: 0.45rem 0.75rem !important;
        }
        [data-baseweb="option"]:hover {
            background: rgba(10, 108, 194, 0.08) !important;
            color: var(--tv-primary) !important;
        }
        input[type="text"]::placeholder,
        input[type="password"]::placeholder,
        textarea::placeholder {
            color: rgba(10, 34, 58, 0.45);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@contextmanager
def tv_card(title: Optional[str] = None, subtitle: Optional[str] = None, badge: Optional[str] = None) -> None:
    """Reusable card wrapper using Streamlit's bordered container."""
    container = st.container(border=True)
    with container:
        if badge:
            st.markdown(f"<div class='tv-pill small'>{escape(badge)}</div>", unsafe_allow_html=True)
        if title:
            st.markdown(f"<h3 class='tv-card-title'>{escape(title)}</h3>", unsafe_allow_html=True)
        if subtitle:
            st.markdown(f"<p class='tv-card-subtitle'>{escape(subtitle)}</p>", unsafe_allow_html=True)
        yield


def section_heading(title: str, subtitle: Optional[str] = None) -> None:
    st.markdown(
        f"""
        <div class="tv-section-title">
            <div class="tv-pill small">Workspace</div>
            <h3 style="margin:0;">{escape(title)}</h3>
            {'<p style="margin:0.25rem 0 0;color:rgba(15,23,42,0.6);">'
             + escape(subtitle) + '</p>' if subtitle else ''}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _clean_handle(handle: Optional[str]) -> str:
    return str(handle or "").strip().lstrip("@")


def _coerce_demographics(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _extract_details(demographics: Dict[str, Any]) -> Dict[str, Any]:
    details = demographics.get("details")
    return details if isinstance(details, dict) else {}


def _resolve_profile_image(handle: Optional[str], demographics: Dict[str, Any]) -> str:
    image = demographics.get("image_url")
    if not image:
        details = _extract_details(demographics)
        image = (
            details.get("Profile Image")
            or details.get("profile_image")
            or details.get("Image URL")
        )
    if image:
        return str(image)
    seed = quote_plus(handle or "creator")
    return f"https://api.dicebear.com/7.x/initials/png?seed={seed}&backgroundColor=EEF2FF&fontFamily=Montserrat"


def _extract_social_links(demographics: Dict[str, Any]) -> List[str]:
    details = _extract_details(demographics)
    links = details.get("Social Links")
    if isinstance(links, list):
        return [str(link) for link in links if link]
    if isinstance(links, str) and links:
        return [links]
    direct_link = demographics.get("profile_url") or demographics.get("publish_link")
    return [str(direct_link)] if direct_link else []


def _resolve_profile_link(handle: Optional[str], platform: Optional[str], demographics: Dict[str, Any]) -> Optional[str]:
    social_links = _extract_social_links(demographics)
    if social_links:
        return social_links[0]
    clean = _clean_handle(handle)
    if not clean:
        return None
    base = PLATFORM_PROFILE_URLS.get((platform or "").lower())
    if not base:
        return None
    return base.format(handle=clean)


def _extract_bio(demographics: Dict[str, Any]) -> Optional[str]:
    bio = demographics.get("bio")
    if bio and str(bio).strip():
        return str(bio)
    details = _extract_details(demographics)
    about = details.get("About")
    if about and str(about).strip() and str(about).strip().upper() != "N/A":
        return str(about)
    return None


def _format_followers(value: Optional[int]) -> str:
    if value is None:
        return "—"
    thresholds = [
        (1_000_000_000, "B"),
        (1_000_000, "M"),
        (1_000, "K"),
    ]
    for boundary, suffix in thresholds:
        if value >= boundary:
            return f"{value / boundary:.1f}{suffix}"
    return f"{value:,}"


def _star_spans(value: float) -> str:
    stars = []
    remaining = value
    for _ in range(5):
        if remaining >= 1:
            stars.append('<span class="tv-star">&#9733;</span>')
            remaining -= 1
        elif remaining >= 0.5:
            stars.append('<span class="tv-star half">&#9733;</span>')
            remaining = 0
        else:
            stars.append('<span class="tv-star empty">&#9733;</span>')
    return "".join(stars)


def _render_star_row(label: str, score: Optional[float]) -> None:
    value = max(0.0, min(5.0, float(score or 0.0)))
    stars = _star_spans(value)
    st.markdown(
        f"""
        <div class="tv-star-block">
            <div class="tv-star-header">
                <div class="tv-star-label">{escape(label)}</div>
                <div class="tv-star-score">{value:.1f}/5</div>
            </div>
            <div class="tv-star-icons">{stars}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _link_badge(link: str) -> str:
    parsed = urlparse(link)
    netloc = parsed.netloc.lower()
    display = netloc.replace("www.", "") or "Link"
    badge = {"icon": "🔗", "label": display, "color": "#0A6CC2"}
    platform_key: Optional[str] = None
    for key, values in LINK_ICON_MAP.items():
        if key in netloc:
            badge = values
            if key in ("fb.com", "facebook"):
                platform_key = "facebook"
            elif key in ("x.com", "twitter"):
                platform_key = "x"
            else:
                platform_key = key
            break
    if not platform_key:
        for key in ("instagram", "tiktok", "youtube", "facebook", "x"):
            if key in netloc:
                platform_key = key
                badge = LINK_ICON_MAP.get(key, badge)
                break
    color = badge["color"]
    icon_data = _get_icon_data(platform_key)
    label = badge["label"]
    short_path = parsed.path.strip("/")
    if short_path:
        if len(short_path) > 12:
            short_path = short_path[:12] + "…"
        display = f"{display}/{short_path}"
    if icon_data:
        mime, encoded = icon_data
        icon_markup = (
            f"<img src='data:{mime};base64,{encoded}' alt='{escape(label)}' "
            f"style='width:20px;height:20px;'/>"
        )
    else:
        icon_markup = f"<span class='icon'>{badge['icon']}</span>"
    return (
        f"<a class='tv-link-badge' href='{escape(link)}' target='_blank' "
        f"style='border-color:{color};color:{color};background:rgba(10,108,194,0.06);'>"
        f"{icon_markup}"
        f"<span class='tv-link-label'>{escape(label)}</span>"
        "</a>"
    )


def _info_pill(label: str, value: str) -> str:
    return (
        "<div class='tv-info-pill'>"
        f"<span>{escape(label)}</span>"
        f"<strong>{escape(value)}</strong>"
        "</div>"
    )


def _get_icon_data(key: Optional[str]) -> Optional[tuple[str, str]]:
    if not key:
        return None
    norm_key = key.lower()
    path = ICON_FILES.get(norm_key)
    if not path or not path.exists():
        return None
    cached = ICON_CACHE.get(norm_key)
    if cached:
        return cached
    suffix = path.suffix.lower()
    mime_map = {
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    mime = mime_map.get(suffix, "image/png")
    ICON_CACHE[norm_key] = (mime, base64.b64encode(path.read_bytes()).decode("utf-8"))
    return ICON_CACHE[norm_key]


def run_with_timer(label: str, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """
    Execute `func` in a worker thread while streaming elapsed time to the UI.
    """
    placeholder = st.empty()
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        while not future.done():
            elapsed = time.perf_counter() - start
            placeholder.info(f"{label} • {elapsed:.1f}s elapsed")
            time.sleep(0.2)
        try:
            result = future.result()
        except Exception:
            elapsed = time.perf_counter() - start
            placeholder.error(f"{label} failed after {elapsed:.1f}s")
            raise
    elapsed = time.perf_counter() - start
    placeholder.success(f"{label} finished in {elapsed:.1f}s")
    return result


def main() -> None:
    st.set_page_config(page_title="True Vibe Tool", layout="wide")
    inject_styles()
    database.init_db()
    init_session_state()
    render_header()
    if st.session_state.user:
        render_application()
    else:
        render_auth()


def render_header() -> None:
    st.markdown(
        """
        <div class="tv-hero">
            <div class="tv-pill">TrueVibe 2.0</div>
            <h1>True Vibe Tool</h1>
            <p>Premium scoring workspace for analysts to evaluate influence across Reach, Interest, Engagement, Content, Authority, and Values.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.session_state.user:
        cols = st.columns([3.5, 1.2], gap="medium")
        cols[0].markdown(
            f"<div class='tv-status-chip'>Signed in as {escape(st.session_state.user['full_name'])}</div>",
            unsafe_allow_html=True,
        )
        logout_clicked = cols[1].button("Log out", key="logout_btn", use_container_width=True)
        if logout_clicked:
            st.session_state.user = None
            st.session_state.active_campaign_id = None
            st.rerun()


def render_auth() -> None:
    tab_login, tab_register = st.tabs(["Log In", "Register"])
    with tab_login:
        with tv_card("Log in", "Enter your workspace credentials to continue.", badge="Access"):
            with st.form("login_form"):
                email = st.text_input("Work email")
                password = st.text_input("Password", type="password")
                submitted = st.form_submit_button("Log in")
                if submitted:
                    handle_login(email, password)
    with tab_register:
        with tv_card("Create an account", "Spin up a premium scoring space for your team.", badge="New"):
            with st.form("register_form"):
                full_name = st.text_input("Full name")
                email = st.text_input("Work email", key="register_email")
                password = st.text_input("Password", type="password", key="register_password")
                submitted = st.form_submit_button("Create account")
                if submitted:
                    handle_registration(full_name, email, password)


def handle_login(email: str, password: str) -> None:
    if not email or not password:
        st.error("Email and password are required.")
        return
    user = database.get_user_by_email(email)
    if not user or not auth.verify_password(password, user["password_hash"]):
        st.error("Invalid credentials.")
        return
    st.session_state.user = user
    st.success("Authenticated successfully.")
    st.rerun()


def handle_registration(full_name: str, email: str, password: str) -> None:
    if not full_name or not email or not password:
        st.error("All fields are required.")
        return
    try:
        hashed = auth.hash_password(password)
        user_id = database.create_user(email=email, full_name=full_name, password_hash=hashed)
        user = database.get_user_by_email(email)
        st.session_state.user = user
        st.success(f"Account created (user id {user_id}).")
        st.rerun()
    except sqlite3.IntegrityError:
        st.error("An account with this email already exists.")


def render_application() -> None:
    options = list(NAV_SECTIONS.keys())
    active_view = st.session_state.active_view if st.session_state.active_view in options else options[0]
    with tv_card("Navigation", "Jump between workspace modules.", badge="Menu"):
        nav_cols = st.columns(len(options))
        for option, col in zip(options, nav_cols):
            with col:
                is_active = option == active_view
                if st.button(
                    option,
                    use_container_width=True,
                    type="primary" if is_active else "secondary",
                    key=f"nav_btn_{option}",
                ):
                    active_view = option
                    st.session_state.active_view = option
        st.caption(NAV_SECTIONS.get(active_view, ""))

    if active_view == "Campaign Briefs":
        render_campaigns_tab()
    elif active_view == "Score Influencers":
        render_kol_workflow_tab()
    else:
        render_dashboard_tab()


def render_campaigns_tab() -> None:
    left_col, right_col = st.columns(2, gap="large")
    with left_col:
        with tv_card("Create a campaign", "Launch a scoring workspace tied to a client brief.", badge="Setup"):
            with st.form("campaign_form"):
                name = st.text_input("Campaign name")
                client = st.text_input("Client")
                market = st.selectbox(
                    "Market",
                    options=[
                        "Indonesia",
                        "Thailand",
                        "Vietnam",
                        "Philippines",
                        "Myanmar",
                        "Singapore",
                        "Malaysia"
                       
                    ],
                )
                timeline_col_from, timeline_col_to = st.columns(2)
                with timeline_col_from:
                    timeline_start = st.date_input(
                        "Campaign start",
                        value=None,
                        format="YYYY-MM-DD",
                        key="campaign_start",
                    )
                with timeline_col_to:
                    timeline_end = st.date_input(
                        "Campaign end",
                        value=None,
                        format="YYYY-MM-DD",
                        key="campaign_end",
                    )
                objective = st.selectbox(
                    "Objective / brief",
                    options=[
                        "Awareness: elevate brand visibility",
                        "Awareness: drive product launch buzz",
                        "Consideration: showcase product benefits",
                        "Consideration: highlight case studies/testimonials",
                        "Conversion: drive signups/sales",
                        "Conversion: retarget existing customer segments",
                        "Advocacy: nurture community storytelling",
                        "Recruitment: discover influencer shortlist",
                        "Other",
                    ],
                )
                objective_notes = st.text_area(
                    "Additional objective context (optional)",
                    placeholder="Add notes or custom brief details…",
                )
                submitted = st.form_submit_button("Create campaign")
                if submitted:
                    if not name:
                        st.error("Campaign name is required.")
                    else:
                        timeline_display = (
                            ""
                            if not timeline_start and not timeline_end
                            else f"Timeline: {timeline_start or 'TBD'} → {timeline_end or 'TBD'}"
                        )
                        objective_text = (
                            f"{objective} — {objective_notes.strip()}"
                            if objective == "Other" and objective_notes
                            else (objective_notes or objective)
                        )
                        composed_objective = "\n".join(
                            filter(None, [objective_text, timeline_display])
                        )
                        campaign_id = database.create_campaign(
                            owner_user_id=st.session_state.user["id"],
                            name=name,
                            client_name=client,
                            market=market,
                            objective=composed_objective,
                        )
                        st.success(f"Campaign {name} created !")

    campaigns = database.list_campaigns_for_user(st.session_state.user["id"])
    with right_col:
        if not campaigns:
            st.info("No campaigns yet. Create one above.")
        else:
            campaign_labels = [f"{c['name']} | {c.get('market') or 'N/A'}" for c in campaigns]
            try:
                active_index = [c["id"] for c in campaigns].index(st.session_state.active_campaign_id)
            except ValueError:
                active_index = 0
            with tv_card("Your Campaigns", "Switch focus and keep the key brief top of mind.", badge="Pipeline"):
                selection = st.selectbox(
                    "Active campaign",
                    options=campaign_labels,
                    index=active_index,
                    key="campaign_select",
                )
                active_campaign = campaigns[campaign_labels.index(selection)]
                st.session_state.active_campaign_id = active_campaign["id"]
                raw_objective = (active_campaign.get("objective") or "").strip()
                timeline_text = "-"
                if raw_objective:
                    filtered_lines: List[str] = []
                    for line in raw_objective.splitlines():
                        stripped = line.strip()
                        if not stripped:
                            continue
                        if stripped.lower().startswith("timeline:"):
                            timeline_text = stripped.split(":", 1)[1].strip() or "-"
                        else:
                            filtered_lines.append(stripped)
                    objective_text = "\n".join(filtered_lines).strip() or "-"
                else:
                    objective_text = "-"
                timeline_text = timeline_text.replace("\x1a", " - ").replace("\u2023", " - ")
                truncated_objective = objective_text if len(objective_text) <= 260 else f"{objective_text[:257].rstrip()}..."
                objective_markup = escape(truncated_objective).replace("\n", "<br>")
                created_at_display = "-"
                created_at_raw = active_campaign.get("created_at")
                if created_at_raw:
                    try:
                        created_at_display = datetime.fromisoformat(created_at_raw).strftime("%b %d, %Y")
                    except ValueError:
                        created_at_display = created_at_raw.split("T")[0]
                kol_count = len(database.list_campaign_influencers(active_campaign["id"]))
                source_count = len(database.list_kol_sources(active_campaign["id"]))
                client_display = active_campaign.get("client_name") or "-"
                market_display = active_campaign.get("market") or "-"
                st.markdown(
                    f"""
                    <div class="tv-campaign-summary">
                        <div class="tv-campaign-summary__header">
                            <p class="tv-campaign-eyebrow">Active campaign</p>
                            <h4>{escape(active_campaign['name'])}</h4>
                            <div class="tv-campaign-tags">
                                <span class="tv-campaign-tag">{escape(client_display)}</span>
                                <span class="tv-campaign-tag">{escape(market_display)}</span>
                                <span class="tv-campaign-tag">Briefed {escape(created_at_display)}</span>
                            </div>
                        </div>
                        <div class="tv-campaign-objective">
                            <span>Objective focus</span>
                            <p>{objective_markup}</p>
                        </div>
                        <div class="tv-campaign-stats">
                            <div class="tv-campaign-stat">
                                <label>Timeline</label>
                                <strong>{escape(timeline_text or "-")}</strong>
                            </div>
                            <div class="tv-campaign-stat">
                                <label>KOLs tracked</label>
                                <strong>{kol_count}</strong>
                            </div>
                            <div class="tv-campaign-stat">
                                <label>Imports logged</label>
                                <strong>{source_count}</strong>
                            </div>
                            <div class="tv-campaign-stat">
                                <label>Brief added</label>
                                <strong>{escape(created_at_display)}</strong>
                            </div>
                        </div>
                        <div class="tv-campaign-meta-grid">
                            <div>
                                <span>Client</span>
                                <p>{escape(client_display)}</p>
                            </div>
                            <div>
                                <span>Market</span>
                                <p>{escape(market_display)}</p>
                            </div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
    if not campaigns:
        return
    active_campaign = next(
        (c for c in campaigns if c["id"] == st.session_state.active_campaign_id),
        campaigns[0],
    )
    render_campaign_ingestion_controls(active_campaign)


def render_campaign_ingestion_controls(campaign: Dict[str, Any]) -> None:
    st.markdown(f"### Manage creators for {escape(campaign['name'])}")
    with tv_card("Add KOLs", "Drop a publish KOL list to ingest data.", badge="Ingest"):
        with st.form(f"kol_link_form_{campaign['id']}"):
            publish_link = st.text_input("Publish link / profile URL")
            count_col, button_col = st.columns([2, 1])
            with count_col:
                max_profiles = st.number_input(
                    "Number of profiles to scrape",
                    min_value=1,
                    max_value=500,
                    value=4,
                    step=1,
                    help="Check the number of profiles to import from the CreatorIQ report",
                    key=f"import_count_{campaign['id']}",
                )
            with button_col:
                st.markdown("<div style='height:1.8rem;'></div>", unsafe_allow_html=True)
                submitted = st.form_submit_button("Import KOLs", use_container_width=True)
            if submitted:
                if not publish_link:
                    st.error("Please provide a publish link.")
                else:
                    try:
                        if is_creatoriq_link(publish_link):
                            try:
                                summary = run_with_timer(
                                    "Importing KOL list from your Publish Link",
                                    ingestion.ingest_creatoriq_report_dom,
                                    campaign["id"],
                                    publish_link,
                                    max_profiles=int(max_profiles),
                                    detail_limit=int(max_profiles),
                                )
                            except Exception as dom_error:
                                st.warning(f"DOM scraper failed ({dom_error}). Attempting CreatorIQ API fallback.")
                                summary = run_with_timer(
                                    "CreatorIQ API import",
                                    ingestion.ingest_creatoriq_report,
                                    campaign["id"],
                                    publish_link,
                                )
                            count = summary.get("count", 0)
                            st.success(f"Imported {count} creator(s) from the CreatorIQ report.")
                            for warning in summary.get("warnings", []):
                                st.warning(warning)
                        else:
                            profile = run_with_timer(
                                "Fetching profile",
                                scraping.fetch_kol_profile,
                                publish_link,
                            )
                            influencer = database.upsert_influencer(profile)
                            database.ensure_campaign_influencer(campaign["id"], influencer["id"])
                            database.add_kol_source(
                                campaign_id=campaign["id"],
                                publish_link=publish_link,
                                platform=profile["platform"],
                                payload=profile,
                                status="ingested",
                            )
                            st.success(f"Added {influencer['name']} ({influencer['platform']}).")
                        st.rerun()
                    except CreatorIQError as err:
                        st.error(f"CreatorIQ import failed: {err}")
                    except Exception as exc:
                        st.error(f"Unable to ingest link: {exc}")

    sources = database.list_kol_sources(campaign["id"])
    if sources:
        with tv_card("Recent KOL List", "Latest CreatorIQ imports and single profiles.", badge="History"):
            st.markdown("##### Latest KOL entries")
            source = sources[0]
            platform = source.get("platform") or "Unknown"
            link = source.get("publish_link") or "-"
            status = source.get("status") or "-"
            if link and link != "-":
                link_markup = f"<a href='{escape(link)}' target='_blank' style='color:#0A6CC2;font-weight:600;text-decoration:none;'>{escape(link)}</a>"
            else:
                link_markup = "<span style='color:rgba(10,34,58,0.55);'>No link</span>"
            st.markdown(
                f"""
                <div style="margin-bottom:0.6rem;">
                    <div style="font-weight:600;color:#0A223A;">
                        {escape(platform)}
                        <span style="margin-left:0.5rem;color:rgba(10,34,58,0.65);text-transform:uppercase;font-size:0.75rem;font-weight:600;">
                            {escape(status)}
                        </span>
                    </div>
                    <div style="font-size:0.85rem;margin-top:0.2rem;">{link_markup}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        raw_profile_rows: List[Dict[str, Any]] = []
        for source in sources:
            payload: Dict[str, Any] = {}
            raw_payload = source.get("raw_payload")
            if raw_payload:
                try:
                    payload = json.loads(raw_payload)
                except json.JSONDecodeError:
                    payload = {}
            profiles = payload.get("profiles") if isinstance(payload, dict) else None
            if isinstance(profiles, list):
                for profile in profiles:
                    details = profile.get("Details")
                    row = {
                        "Source": source.get("publish_link"),
                        "Full Name": profile.get("Full Name"),
                        "Handle": profile.get("Handle"),
                        "Platform": profile.get("Platform"),
                        "Followers": profile.get("Followers"),
                        "Bio": profile.get("Bio"),
                        "Image": profile.get("Image URL"),
                    }
                    row.update(_flatten_details(details))
                    raw_profile_rows.append(row)
        if raw_profile_rows:
            with tv_card("Raw imported profiles", "Deep dive on the JSON pulled from CreatorIQ.", badge="Diagnostics"):
                raw_df = pd.DataFrame(raw_profile_rows)
                st.dataframe(raw_df, use_container_width=True, hide_index=True)
        else:
            st.info("Raw profile payloads not available for these sources.")



def render_kol_workflow_tab() -> None:
    campaign = get_active_campaign()
    if not campaign:
        st.warning("Select a campaign in the Campaign Briefs tab to start scoring.")
        return
    section_heading(f"KOL workflow • {campaign['name']}", "Scrape CreatorIQ reports or add individual publish links.")
    left_col, right_col = st.columns([1.1, 1.9], gap="large")


    with left_col:
        with tv_card("KOL Pool", "Reuse existing creators imported by your team.", badge="Pool"):
            search_value = st.text_input(
                "Search by name, handle, or platform",
                key=f"kol_pool_search_{campaign['id']}",
                placeholder="@handle, creator name, TikTok...",
            )
            pool_rows = database.list_all_influencers(search_value)
            if not pool_rows:
                st.info("No creators match this search yet.")
            else:
                pool_df = pd.DataFrame(pool_rows)
                display_cols = ["name", "handle", "platform", "follower_count", "last_seen_at"]
                display_df = pool_df[display_cols]
                st.dataframe(display_df, use_container_width=True, hide_index=True)
                option_map: Dict[str, Dict[str, Any]] = {}
                for row in pool_rows:
                    name = row.get("name") or "Unknown"
                    handle = row.get("handle") or "-"
                    platform = row.get("platform") or "Unknown"
                    label = f"{name} (@{handle}) - {platform}"
                    option_map[label] = row
                selection = st.selectbox(
                    "Select a KOL to add to this campaign",
                    options=list(option_map.keys()),
                    key=f"kol_pool_select_{campaign['id']}",
                )
                if st.button("Add selected KOL", key=f"kol_pool_add_{campaign['id']}"):
                    chosen = option_map[selection]
                    database.ensure_campaign_influencer(campaign["id"], chosen["id"])
                    st.success(f"Added {chosen['name']} to {campaign['name']}.")
                    st.rerun()

    with right_col:
        render_scoring_form(campaign)


def render_scoring_form(campaign: Dict[str, Any]) -> None:
    kol_rows = database.list_campaign_influencers(campaign["id"])
    if not kol_rows:
        st.info("No KOLs linked to this campaign yet.")
        return
    enriched_rows = []
    for row in kol_rows:
        status = "Scored" if row.get("total_score") else "Unscored"
        enriched = dict(row)
        enriched["status"] = status
        enriched_rows.append(enriched)
    platforms = sorted({row["platform"] or "Unknown" for row in enriched_rows})
    filter_cols = st.columns(2)
    with filter_cols[0]:
        platform_choice = st.selectbox(
            "Filter by platform",
            options=["All platforms"] + platforms,
            key="platform_filter",
        )
        selected_platforms = platforms if platform_choice == "All platforms" else [platform_choice]
    with filter_cols[1]:
        status_choice = st.selectbox(
            "Scoring status",
            options=["All statuses", "Unscored", "Scored"],
            key="status_filter",
        )
    filtered_rows = [
        row
        for row in enriched_rows
        if (row["platform"] or "Unknown") in selected_platforms
        and (status_choice == "All statuses" or row["status"] == status_choice)
    ]
    if not filtered_rows:
        st.info("No KOLs match the current filters.")
        return
    option_labels = []
    label_map: Dict[str, Dict[str, Any]] = {}
    for row in filtered_rows:
        badge = "✓" if row["status"] == "Scored" else "•"
        label = f"{row['name']} ({row['platform']}) {badge}"
        option_labels.append(label)
        label_map[label] = row
    with tv_card("Profile & Score overview", "Review scraped data plus automated Reach/Interest/Engagement metrics.", badge="Profile & Score"):
        st.markdown(
            "<div class='tv-select-hint'>Tap to open the list and choose the KOL to score</div>",
            unsafe_allow_html=True,
        )
        selection = st.selectbox("Select an KOL for scoring", options=option_labels)
        selected = label_map[selection]
        st.caption("Status: " + ("Already scored" if selected["status"] == "Scored" else "Not yet scored"))
        demographics: Dict[str, Any] = {}
        raw_demographics = selected.get("demographics_json")
        if raw_demographics:
            try:
                demographics = json.loads(raw_demographics)
            except json.JSONDecodeError:
                demographics = {}
        demographics = _coerce_demographics(demographics)
        quant_scores = _derive_quant_scores(
            follower_count=selected.get("follower_count"),
            demographics=demographics,
            campaign_objective=campaign.get("objective"),
            fallback_row=selected,
        )
        profile_image = _resolve_profile_image(selected.get("handle"), demographics)
        profile_link = _resolve_profile_link(selected.get("handle"), selected.get("platform"), demographics)
        social_links = _extract_social_links(demographics)
        bio_text = _extract_bio(demographics)
        handle_display = _clean_handle(selected.get("handle"))
        profile_cols = st.columns([1, 2], gap="large")
        with profile_cols[0]:
            st.image(
                profile_image,
                caption=selected.get("platform") or "—",
                use_container_width=True,
            )
            link_targets: List[str] = []
            for link in social_links:
                if link and link not in link_targets:
                    link_targets.append(link)
            if profile_link and profile_link not in link_targets:
                link_targets.insert(0, profile_link)
            if link_targets:
                st.markdown("**Links**")
                badges = "".join(_link_badge(link) for link in link_targets[:4])
                st.markdown(f"<div class='tv-link-grid'>{badges}</div>", unsafe_allow_html=True)
        with profile_cols[1]:
            platform_key = (selected.get("platform") or "").lower()
            icon_data = _get_icon_data(platform_key)
            if icon_data:
                mime, encoded = icon_data
                icon_markup = (
                    f"<img src='data:{mime};base64,{encoded}' alt='{escape(platform_key or 'platform')}' "
                    f"style='width:26px;height:26px;'/>"
                )
            else:
                icon_markup = ""
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:0.4rem;'>"
                f"{icon_markup}"
                f"<h3 style='margin:0;'>{escape(selected['name'])}</h3></div>",
                unsafe_allow_html=True,
            )
            if handle_display:
                st.markdown(
                    f"<p style='margin-top:0;color:rgba(15,23,42,0.7);font-weight:600;'>@{escape(handle_display)}</p>",
                    unsafe_allow_html=True,
                )
            if bio_text:
                st.write(bio_text)
        info_cols = st.columns(3)
        with info_cols[0]:
            st.markdown(
                _info_pill("Followers", _format_followers(selected.get("follower_count"))),
                unsafe_allow_html=True,
            )
        with info_cols[1]:
            st.markdown(
                _info_pill("Engagement rate", f"{quant_scores['engagement_rate']:.2f}%"),
                unsafe_allow_html=True,
            )
        with info_cols[2]:
            st.markdown(
                _info_pill("Platform", selected.get("platform") or "—"),
                unsafe_allow_html=True,
            )
        saved_balance_raw = selected.get("content_balance")
        saved_balance_score = _safe_float(saved_balance_raw, default=None) if saved_balance_raw is not None else None
        engagement_display = quant_scores["engagement_score"]
        if saved_balance_score is not None:
            engagement_display = round(
                (engagement_display * 0.7) + (saved_balance_score * 0.3),
                2,
            )
        star_cols = st.columns(3)
        with star_cols[0]:
            _render_star_row("Reach score", quant_scores["reach_score"])
        with star_cols[1]:
            _render_star_row("Interest score", quant_scores["interest_score"])
        with star_cols[2]:
            _render_star_row("Engagement score", engagement_display)
            if saved_balance_score is not None:
                st.caption(f"Content balance: {saved_balance_score:.1f}")

    with tv_card("Manual scoring", "Provide your qualitative inputs.", badge="Score input"):
        suffix = selected.get("campaign_influencer_id")
        with st.form(f"score_form_{suffix}"):
            organic_default = float(_safe_float(selected.get("organic_posts_l2m"), 0.0) or 0.0)
            sponsored_default = float(_safe_float(selected.get("sponsored_posts_l2m"), 0.0) or 0.0)
            saturation_cols = st.columns(2, gap="large")
            with saturation_cols[0]:
                organic_posts = st.number_input(
                    "Estimated organic posts (last 2 months)",
                    min_value=0.0,
                    value=organic_default,
                    step=1.0,
                    key=f"organic_posts_{suffix}",
                )
            with saturation_cols[1]:
                sponsored_posts = st.number_input(
                    "Estimated sponsored posts (last 2 months)",
                    min_value=0.0,
                    value=sponsored_default,
                    step=1.0,
                    key=f"sponsored_posts_{suffix}",
                )
            saturation_rate = _compute_saturation_rate(organic_posts, sponsored_posts)
            content_balance_score = _content_balance_score_from_rate(saturation_rate)
            base_engagement = quant_scores["engagement_score"]
            engagement_preview = base_engagement
            if content_balance_score is not None:
                engagement_preview = round(
                    (base_engagement * 0.7) + (content_balance_score * 0.3),
                    2,
                )
                st.caption(
                    f"Content balance score: {content_balance_score:.1f}  |  "
                    f"Saturation rate: {saturation_rate:.2f}  |  "
                    f"Engagement score preview: {engagement_preview:.2f}"
                )
            else:
                st.caption("Add both estimates (sponsored posts must be > 0) to factor balance into engagement/content.")
            manual_cols = st.columns(2, gap="large")
            with manual_cols[0]:
                content_originality = st.slider(
                    "Originality - recognizable identity via passions/values (1-5)",
                    min_value=1.0,
                    max_value=5.0,
                    value=float(selected.get("content_originality") or 3.0),
                    step=0.5,
                    key=f"content_originality_{suffix}",
                )
                authority_overall = st.slider(
                    "Authority - relevant credentials + clean reputation (1-5)",
                    min_value=1.0,
                    max_value=5.0,
                    value=float(selected.get("authority_score") or 3.0),
                    step=0.5,
                    key=f"authority_overall_{suffix}",
                )
            with manual_cols[1]:
                content_creativity = st.slider(
                    "Creative - distinctive/original content (1-5)",
                    min_value=1.0,
                    max_value=5.0,
                    value=float(selected.get("content_creativity") or 3.0),
                    step=0.5,
                    key=f"content_creativity_{suffix}",
                )
                values_overall = st.slider(
                    "Values - aligned statements in past 3-6 months (1-5)",
                    min_value=1.0,
                    max_value=5.0,
                    value=float(selected.get("values_score") or 3.0),
                    step=0.5,
                    key=f"values_overall_{suffix}",
                )
            notes = st.text_area("Qualitative notes", value=selected.get("qualitative_notes") or "")
            submitted = st.form_submit_button("Save score")
            if submitted:
                payload = scoring.build_score_payload(
                    reach_score=quant_scores["reach_score"],
                    interest_score=quant_scores["interest_score"],
                    engagement_rate=quant_scores["engagement_rate"],
                    engagement_score=engagement_preview,
                    content_balance_score=content_balance_score,
                    organic_posts_l2m=organic_posts,
                    sponsored_posts_l2m=sponsored_posts,
                    saturation_rate=saturation_rate,
                    content_originality=content_originality,
                    content_creativity=content_creativity,
                    authority_overall=authority_overall,
                    values_overall=values_overall,
                    qualitative_notes=notes,
                )
                database.save_campaign_influencer_scores(selected["campaign_influencer_id"], payload)
                st.success("Score saved!")



def render_dashboard_tab() -> None:
    user = st.session_state.user
    campaigns = database.list_campaigns_for_user(user["id"])
    if not campaigns:
        st.info("No campaigns yet. Create one first.")
        return

    def _market_label(c: Dict[str, Any]) -> str:
        return c.get("market") or "Unspecified"

    markets = sorted({_market_label(c) for c in campaigns})
    with tv_card("Dashboard filters", "Slice performance by market, campaign, and KOL.", badge="Filters"):
        filter_cols = st.columns(2)
        with filter_cols[0]:
            market_choice = st.selectbox(
                "Market",
                ["All markets"] + markets,
                key="dash_market_filter",
            )
        filtered_campaigns = [
            c for c in campaigns if market_choice == "All markets" or _market_label(c) == market_choice
        ]
        if not filtered_campaigns:
            st.info("No campaigns match this market filter.")
            return
        label_map = {
            f"{c['name']} ({_market_label(c)})": c for c in filtered_campaigns
        }
        campaign_labels = list(label_map.keys())
        default_label = campaign_labels[0]
        active_id = st.session_state.get("active_campaign_id")
        if active_id:
            for label, campaign in label_map.items():
                if campaign["id"] == active_id:
                    default_label = label
                    break
        with filter_cols[1]:
            campaign_label = st.selectbox(
                "Campaign",
                campaign_labels,
                index=campaign_labels.index(default_label),
                key="dash_campaign_filter",
            )
    selected_campaign = label_map[campaign_label]
    rows = database.list_dashboard_rows(selected_campaign["id"])
    if not rows:
        st.info("No scored KOLs yet for this campaign.")
        return
    df = pd.DataFrame(rows)
    section_heading(
        f"True Vibe dashboard • {selected_campaign['name']}",
        "Monitor momentum at a glance and export the scoring grid for stakeholders.",
    )

    search = st.text_input("Search KOLs", placeholder="Search by name", key="dash_kol_search")
    kol_options = sorted(df["name"].dropna().unique().tolist())
    filtered_names = [name for name in kol_options if search.lower() in name.lower()]
    selected_names = st.multiselect(
        "Focus KOL(s)",
        options=filtered_names,
        default=filtered_names[:1],
        key="dash_kol_filter",
    )
    selected_row = None
    if selected_names:
        filtered = df[df["name"].isin(selected_names)]
        if not filtered.empty:
            selected_row = filtered.iloc[0]

    completed = df["total_score"].notnull().sum()
    total_records = len(df)
    completion_pct = (completed / total_records) * 100 if total_records else 0
    scored_df = df.dropna(subset=["total_score"])
    avg_total = scored_df["total_score"].mean() if not scored_df.empty else None
    top_row = scored_df.sort_values("total_score", ascending=False).iloc[0] if not scored_df.empty else None

    with tv_card("Progress overview", "Key performance signals across the roster.", badge="Snapshot"):
        metric_cols = st.columns(3)
        metric_cols[0].metric(
            "Scored creators",
            f"{completed}/{total_records}",
            delta=f"{completion_pct:.0f}% complete" if total_records else None,
        )
        metric_cols[1].metric(
            "Average total score",
            f"{avg_total:.2f}" if avg_total is not None else "—",
            delta=None,
        )
        metric_cols[2].metric(
            "Top performer",
            f"{top_row['total_score']:.2f}" if top_row is not None else "—",
            delta=top_row["name"] if top_row is not None else None,
        )

    with tv_card("KOL radar", "Visualize TrueVibe dimensions for a single creator.", badge="Focus"):
        if selected_row is None:
            st.info("Select at least one KOL above to preview the radar visualization.")
        else:
            categories = [
                ("Reach", selected_row.get("reach_score")),
                ("Interest", selected_row.get("interest_score")),
                ("Engagement", selected_row.get("engagement_score")),
                ("Content", selected_row.get("content_score")),
                ("Authority", selected_row.get("authority_score")),
                ("Values", selected_row.get("values_score")),
            ]
            r = [max(0.0, min(5.0, float(value or 0.0))) for _, value in categories]
            theta = [label for label, _ in categories]
            radar_fig = go.Figure()
            radar_fig.add_trace(
                go.Scatterpolar(
                    r=r + [r[0]],
                    theta=theta + [theta[0]],
                    fill="toself",
                    line=dict(color="#0A6CC2", width=3),
                    hovertemplate="%{theta}: %{r:.1f}<extra></extra>",
                )
            )
            radar_fig.update_layout(
                polar=dict(
                    radialaxis=dict(range=[0, 5], showticklabels=True, ticks=""),
                    angularaxis=dict(showticklabels=True),
                ),
                showlegend=False,
                margin=dict(l=20, r=20, t=20, b=20),
            )
            radar_cols = st.columns([2, 1])
            radar_cols[0].plotly_chart(radar_fig, use_container_width=True, config={"displayModeBar": False})
            with radar_cols[1]:
                st.markdown(
                    f"<div style='font-size:2.5rem;font-weight:700;color:#0A223A;'>{selected_row.get('total_score') or 0:.1f}</div>",
                    unsafe_allow_html=True,
                )
                st.caption("TrueVibe total score")
                metric_grid = st.columns(2)
                for idx, (label, value) in enumerate(categories):
                    metric_grid[idx % 2].metric(label, f"{value or 0:.1f}")

    with tv_card("Score comparison", "Stack-ranked total scores by platform.", badge="Visualization"):
        if not scored_df.empty:
            chart_df = scored_df.sort_values("total_score", ascending=False)
            fig = px.bar(
                chart_df,
                x="name",
                y="total_score",
                color="platform",
                text="total_score",
                color_discrete_sequence=VERO_COLORWAY,
            )
            fig.update_traces(
                texttemplate="%{text:.1f}",
                textposition="outside",
                hovertemplate="<b>%{x}</b><br>Total score: %{y:.2f}<extra></extra>",
            )
            max_score = float(chart_df["total_score"].max())
            fig.update_layout(
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#0A223A", family="TT Commons Pro, Inter, sans-serif"),
                margin=dict(l=10, r=10, t=15, b=40),
                yaxis=dict(range=[0, max(5.0, min(35.0, max_score + 2))]),
                showlegend=True,
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("Once at least one creator is fully scored, a chart will appear here.")

    with tv_card("Score breakdown", "Full grid of underlying metrics and export.", badge="Data"):
        st.dataframe(
            df[
                [
                    "name",
                    "platform",
                    "follower_count",
                    "reach_score",
                    "interest_score",
                    "engagement_score",
                    "content_score",
                    "authority_score",
                    "values_score",
                    "total_score",
                    "updated_at",
                ]
            ],
            use_container_width=True,
        )
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("Download CSV", data=csv, file_name="truevibe_dashboard.csv", mime="text/csv")


def get_active_campaign() -> Optional[Dict[str, Any]]:
    campaign_id = st.session_state.get("active_campaign_id")
    if not campaign_id:
        return None
    return database.get_campaign(campaign_id)


if __name__ == "__main__":
    main()
