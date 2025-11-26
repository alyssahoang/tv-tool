# True Vibe Streamlit Prototype

This repository hosts a lightweight Streamlit implementation of Vero's TrueVibe 2.0 workflow. The goal is to replace the Google Sheets-based scoring pipeline with a focused web experience backed by SQLite.

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

Environment variables:

- `TRUEVIBE_DB_PATH` (optional): override the default `data/truevibe-db.db` path.

## Seed demo data

Create a demo user, campaign, and a few scored KOLs:

```bash
python scripts/seed_db.py \
  --email analyst@example.com \
  --password demo1234! \
  --full-name "Demo Analyst"
```

Point the app at your custom database if needed:

```bash
set TRUEVIBE_DB_PATH=data/tv-database  # Windows PowerShell
streamlit run app.py
```

## CreatorIQ scraping helper

When a public share link no longer responds to the API layer, you can still ingest it via Selenium:

```bash
python scripts/scrape_creatoriq.py --url https://vero.creatoriq.com/lists/report/<slug> --campaign-id 1 --headless
```

Prerequisites: Chrome installed plus a matching ChromeDriver on PATH. The script loads the page, extracts the Apollo cache the UI uses, and stores every creator in the chosen campaign.

## Features

- Account creation and login with hashed passwords.
- Campaign management view with active campaign selection.
- CreatorIQ ingestion: drop a public report link and the app pulls every KOL in that list via GraphQL.
- Scoring form aligned with the Reach, Interest, Engagement, Content, Authority, and Values dimensions defined in `docs/methodology_summary.md`.
- Dashboard tab displaying score breakdowns, bar charts, and CSV export.

## Project structure

```
app.py                 # Streamlit entrypoint
truevibe/
  config.py            # Paths and environment settings
  database.py          # SQLite schema + data helpers
  auth.py              # Password hashing and verification helpers
  scoring.py           # TrueVibe score calculations
  scraping.py          # Placeholder scraper for publish links
docs/
  methodology_summary.md
data/
  truevibe-db.db       # Default SQLite storage (auto-created)
```

## Next steps

- Replace the scraper stub with production scraping routines (or API connectors like CreatorIQ).
- Add background jobs for refreshing KOL stats and recalculating dashboards.
- Implement granular roles (analyst vs. viewer) and audit logging.
- Harden validation plus add automated tests for scoring and persistence layers.
