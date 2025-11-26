from __future__ import annotations

import argparse
import random
from typing import Dict, List

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from truevibe import auth, database, scoring, scraping  # noqa: E402
from truevibe.config import get_db_path  # noqa: E402


SAMPLE_LINKS = [
    "https://www.tiktok.com/@freshvibes/video/123456789",
    "https://www.instagram.com/p/CIQdemoInfluencer/",
    "https://www.youtube.com/@techpulseHQ",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed the TrueVibe demo database with mock data.")
    parser.add_argument("--email", default="analyst@example.com", help="Seed user email")
    parser.add_argument("--password", default="demo1234!", help="Seed user password")
    parser.add_argument("--full-name", default="Demo Analyst", help="Seed user full name")
    parser.add_argument("--campaign", default="TrueVibe Demo Campaign", help="Campaign name")
    parser.add_argument("--client", default="ACME Corp", help="Client name")
    parser.add_argument("--market", default="Vietnam", help="Market label")
    parser.add_argument("--objective", default="Showcase sample data", help="Campaign objective")
    return parser.parse_args()


def ensure_user(email: str, full_name: str, password: str) -> Dict[str, str]:
    user = database.get_user_by_email(email)
    if user:
        print(f"[seed] Reusing existing user {email} (id={user['id']}).")
        return user
    hashed = auth.hash_password(password)
    user_id = database.create_user(email=email, full_name=full_name, password_hash=hashed)
    print(f"[seed] Created user {email} (id={user_id}).")
    return database.get_user_by_email(email)  # type: ignore[return-value]


def ensure_campaign(
    owner_user_id: int,
    name: str,
    client: str,
    market: str,
    objective: str,
) -> Dict[str, str]:
    for campaign in database.list_campaigns_for_user(owner_user_id):
        if campaign["name"].lower() == name.lower():
            print(f"[seed] Reusing existing campaign '{name}' (id={campaign['id']}).")
            return campaign
    campaign_id = database.create_campaign(
        owner_user_id=owner_user_id,
        name=name,
        client_name=client,
        market=market,
        objective=objective,
    )
    print(f"[seed] Created campaign '{name}' (id={campaign_id}).")
    return database.get_campaign(campaign_id)  # type: ignore[return-value]


def generate_score_payload() -> Dict[str, float | str]:
    reach = random.uniform(2.5, 4.8)
    interest = random.uniform(2.5, 4.8)
    engagement_rate = round(random.uniform(1.5, 6.0), 2)
    engagement_score = random.uniform(2.5, 4.8)
    originality = random.uniform(2.5, 4.8)
    creativity = random.uniform(2.5, 4.8)
    authority = random.uniform(2.5, 4.8)
    values = random.uniform(2.5, 4.8)
    notes = "Auto-seeded sample entry for demo purposes."
    return scoring.build_score_payload(
        reach_score=reach,
        interest_score=interest,
        engagement_rate=engagement_rate,
        engagement_score=engagement_score,
        content_originality=originality,
        content_creativity=creativity,
        authority_overall=authority,
        values_overall=values,
        qualitative_notes=notes,
    )


def seed_influencers(campaign_id: int, publish_links: List[str]) -> None:
    for link in publish_links:
        profile = scraping.fetch_kol_profile(link)
        influencer = database.upsert_influencer(profile)
        join_row = database.ensure_campaign_influencer(campaign_id, influencer["id"])
        database.add_kol_source(
            campaign_id=campaign_id,
            publish_link=link,
            platform=profile["platform"],
            payload=profile,
            status="seeded",
        )
        payload = generate_score_payload()
        database.save_campaign_influencer_scores(join_row["id"], payload)
        print(
            f"[seed] Added {influencer['name']} ({influencer['platform']}) "
            f"to campaign_id={campaign_id} with total score {payload['total_score']}."
        )


def main() -> None:
    args = parse_args()
    database.init_db()
    print(f"[seed] Using SQLite file at {get_db_path()}")
    user = ensure_user(args.email, args.full_name, args.password)
    campaign = ensure_campaign(
        owner_user_id=user["id"],
        name=args.campaign,
        client=args.client,
        market=args.market,
        objective=args.objective,
    )
    seed_influencers(campaign["id"], SAMPLE_LINKS)
    print("[seed] Done. Launch Streamlit (`streamlit run app.py`) to view the seeded data.")


if __name__ == "__main__":
    main()
