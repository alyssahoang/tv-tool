from __future__ import annotations

from hashlib import md5
from typing import Dict
from urllib.parse import urlparse


PLATFORM_HINTS = {
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "youtube": "YouTube",
    "x.com": "X",
    "twitter": "X",
}


def infer_platform(publish_link: str) -> str:
    parsed = urlparse(publish_link)
    netloc = parsed.netloc.lower()
    for hint, platform in PLATFORM_HINTS.items():
        if hint in netloc:
            return platform
    return "Unknown"


def fetch_kol_profile(publish_link: str) -> Dict[str, object]:
    """
    Scrape (stub) a KOL profile from a publish link.

    The current implementation generates deterministic placeholder values so the
    rest of the system can be wired up before integrating a real scraper.
    """
    parsed = urlparse(publish_link)
    platform = infer_platform(publish_link)
    slug = parsed.path.strip("/").split("/")[-1] if parsed.path else ""
    handle = slug or parsed.netloc.split(".")[0]
    fingerprint = int(md5(publish_link.encode("utf-8")).hexdigest(), 16)
    follower_count = 5_000 + (fingerprint % 500_000)
    demographics = {
        "primary_market": parsed.netloc.split(".")[-1],
        "core_age": "18-34",
        "core_gender": "Mixed",
    }
    return {
        "name": handle.replace("-", " ").title() or "Unknown Creator",
        "handle": handle or "unknown",
        "platform": platform,
        "publish_link": publish_link,
        "follower_count": follower_count,
        "demographics": demographics,
    }
