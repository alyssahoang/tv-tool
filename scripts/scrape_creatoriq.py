from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from truevibe import ingestion


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape CreatorIQ share link via Selenium or GraphQL.")
    parser.add_argument("--url", help="Full CreatorIQ share URL")
    parser.add_argument("--slug", help="CreatorIQ share slug (e.g., demo_truevibe-XYZ)")
    parser.add_argument("--campaign-id", type=int, required=True, help="Campaign ID to attach influencers to")
    parser.add_argument(
        "--mode",
        choices=("dom", "apollo"),
        default="dom",
        help="dom = Selenium DOM scraper (default), apollo = GraphQL ingestion.",
    )
    parser.add_argument("--max-profiles", type=int, default=100, help="Max profiles to ingest when using DOM mode.")
    parser.add_argument(
        "--detail-limit",
        type=int,
        default=None,
        help="Profiles to open for detailed scraping (DOM mode only, default is all scraped profiles).",
    )
    parser.add_argument("--headless", action="store_true", default=False, help="Run browser in headless mode (DOM mode).")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.url and not args.slug:
        print("Either --url or --slug must be provided.", file=sys.stderr)
        sys.exit(1)
    publish_link = args.url or f"https://vero.creatoriq.com/lists/report/{args.slug}"
    detail_limit: Optional[int] = args.detail_limit
    try:
        if args.mode == "apollo":
            summary = ingestion.ingest_creatoriq_report(args.campaign_id, publish_link)
            mode_used = "Apollo GraphQL"
        else:
            summary = ingestion.ingest_creatoriq_report_dom(
                args.campaign_id,
                publish_link,
                max_profiles=args.max_profiles,
                detail_limit=detail_limit,
                headless=args.headless,
            )
            mode_used = "DOM Selenium"
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    count = summary.get("count", 0)
    print(f"Imported {count} creator(s) using {mode_used}.")
    for warning in summary.get("warnings", []):
        print(f"Warning: {warning}")


if __name__ == "__main__":
    main()
