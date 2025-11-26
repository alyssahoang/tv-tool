from __future__ import annotations

from typing import Dict, List, Optional

from . import database
from .creatoriq import CreatorIQClient, CreatorIQError, CreatorRecord, extract_slug, is_creatoriq_link
from .creatoriq_dom import CreatorIQDomScraper, normalize_dom_profile


def _normalize_creator_payload(creator: Dict[str, object]) -> Dict[str, object]:
    accounts = creator.get("accounts") or []
    account = accounts[0] if accounts else {}
    follower_count = creator.get("totalSocialConnections") or account.get("followers")
    demographics = {
        "country": creator.get("country"),
        "city": creator.get("city"),
        "gender": creator.get("gender"),
        "language": creator.get("language"),
        "tags": creator.get("tags"),
        "categories": creator.get("categories"),
        "subCategories": creator.get("subCategories"),
    }
    profile = {
        "name": creator.get("fullName") or creator.get("primarySocialUsername"),
        "handle": creator.get("primarySocialUsername") or creator.get("listCreatorsId"),
        "platform": creator.get("primaryNetwork") or account.get("network"),
        "follower_count": follower_count,
        "demographics": demographics,
        "profile_url": account.get("accountUrl"),
        "profile_image": creator.get("profilePictureURL"),
    }
    return profile


def ingest_creatoriq_report(campaign_id: int, publish_link: str) -> Dict[str, object]:
    """
    Pull all creators from a CreatorIQ share link and persist them to the database.

    Returns a summary dictionary with the amount of imported creators and potential warnings.
    """
    if not is_creatoriq_link(publish_link):
        raise ValueError("Link does not belong to CreatorIQ.")

    slug = extract_slug(publish_link)
    client = CreatorIQClient(slug=slug)
    creators_data = client.fetch_creators()
    imported_ids: List[int] = []
    warnings: List[str] = []

    for creator in creators_data:
        creator_id = creator.get("id") or creator.get("listCreatorsId")
        record = CreatorRecord(data=creator, detail=None)
        payload = record.merged()
        profile = _normalize_creator_payload(payload)

        if not profile.get("handle"):
            warnings.append("Skipped a creator because handle/username was missing.")
            continue

        influencer = database.upsert_influencer(
            {
                "name": profile.get("name") or profile["handle"],
                "handle": profile["handle"],
                "platform": profile.get("platform") or "Unknown",
                "follower_count": profile.get("follower_count"),
                "demographics": profile.get("demographics"),
            }
        )
        campaign_influencer = database.ensure_campaign_influencer(campaign_id, influencer["id"])
        imported_ids.append(campaign_influencer["campaign_influencer_id"])

    database.add_kol_source(
        campaign_id=campaign_id,
        publish_link=publish_link,
        platform="CreatorIQ",
        payload={"imported_ids": imported_ids},
        status="imported",
    )
    return {"count": len(imported_ids), "warnings": warnings}


def ingest_creatoriq_report_dom(
    campaign_id: int,
    publish_link: str,
    *,
    max_profiles: int = 100,
    detail_limit: Optional[int] = None,
    headless: bool = True,
) -> Dict[str, object]:
    """
    Scrape a CreatorIQ share link via Selenium DOM traversal when the API is unavailable.
    """
    if not is_creatoriq_link(publish_link):
        raise ValueError("Link does not belong to CreatorIQ.")
    scraper = CreatorIQDomScraper(headless=headless)
    profiles = scraper.scrape_report(
        publish_link,
        max_profiles=max_profiles,
        detail_limit=detail_limit,
    )
    imported_ids: List[int] = []
    warnings: List[str] = []
    for profile in profiles:
        normalized = normalize_dom_profile(profile)
        handle = normalized.get("handle")
        if not handle or handle == "unknown":
            warnings.append("Skipped a creator because handle/username was missing.")
            continue
        influencer = database.upsert_influencer(normalized)
        campaign_influencer = database.ensure_campaign_influencer(campaign_id, influencer["id"])
        imported_ids.append(campaign_influencer["campaign_influencer_id"])
    database.add_kol_source(
        campaign_id=campaign_id,
        publish_link=publish_link,
        platform="CreatorIQ (DOM)",
        payload={"profiles": profiles, "max_profiles": max_profiles},
        status="imported",
    )
    return {"count": len(imported_ids), "warnings": warnings}
