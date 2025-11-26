# TrueVibe 2.0 Methodology Summary

This document distills the scoring guidance captured in `tv_methodology.pdf` so we can keep the Streamlit prototype aligned with the official process.

## Score Model Overview

- Six equally weighted attributes: Reach, Interest, Engagement, Content, Authority, Values.
- Each attribute receives an integer score from 1 (weak) to 5 (excellent) derived from a mix of quantitative metrics and qualitative review.
- The **TrueVibe Score** is the sum of all six attributes with a maximum of 30.
- Interpretation bands:
  - 26–30 → Ideal Fit (flagship campaign ready)
  - 20–25 → Good Alternative (usable but watch weak spots)
  - <20 → Less Ideal (needs major improvement / higher risk)

## Attribute Inputs

| Attribute | What it captures | Primary signals | Notes |
|-----------|------------------|-----------------|-------|
| Reach | Audience size and ability to touch the intended demographic. | CreatorIQ follower counts, demographic match (age, gender, market). | Requires demographic alignment check beyond raw followers. |
| Interest | How relevant the influencer’s content is to the campaign topic. | Top content themes, consistency with requested passion points or trends. | Qualitative reviewers tag interest alignment per campaign brief. |
| Engagement | Depth of audience interaction. | Engagement rate, quality engagements (likes/comments/views vs bots). | Support storing raw rate (%) to show in dashboard. |
| Content | Craft quality showing a recognizable identity and unique ideas. | **Originality** – identity is easily recognized via passions/values; **Creative** – produces distinctive content rooted in own creativity. | Score = average of Originality & Creative sliders (1–5). |
| Authority | Credibility & professionalism. | Combined qualitative prompt: “Has relevant credentials and keeps a clean, controversy-free reputation.” | Single slider (1–5) capturing the combined authority view. |
| Values | Alignment with brand mission and risk profile. | Combined qualitative prompt: “In the past 3–6 months this KOL’s expressed values align with the brand and avoid conflicting views.” | Single slider (1–5) referencing the 3–6 month lookback. |

## Qualitative Prompt Update (2025)

To reduce reviewer workload the qualitative section now uses four prompts:

1. **Originality** – A KOL’s identity is easily recognized through their passions and interests, collaborating with brands aligned to their values and leading/joining social trends.
2. **Creative** – The KOL produces original and distinctive content that showcases their creativity.
3. **Authority** – The KOL holds relevant credentials in their field and maintains a clean, trustworthy reputation free from controversy.
4. **Values** – Within the past 3–6 months the KOL has expressed values aligned with the brand and has avoided opinions that conflict with the brand’s beliefs.

Content uses prompts 1–2, while Authority and Values are each captured with a single slider, keeping the overall TrueVibe score structure intact.

## Operational Workflow

1. **Data intake**
   - Pull quantitative fields (followers, ER, demographics) from CreatorIQ when available.
   - Capture campaign-specific context via the TrueVibe ScoreCard form (manual fields inside the Streamlit UI for this version).
2. **Scoring session**
   - Analysts review each attribute, filling structured inputs (numeric) plus qualitative notes.
   - Content/Authority/Values each rely on subsidiary metrics that must be stored separately so recomputation is traceable.
3. **Aggregation**
   - Convert all component metrics onto a 1–5 scale, round to one decimal, then persist both the raw inputs and the computed attribute score.
   - Sum the six attributes for the TrueVibe Score.
4. **Dashboarding**
   - Surface both total scores and attribute breakdowns.
   - Provide filters by campaign, market, influencer tier, and quick links to external KOL channels.
5. **Reporting**
   - Allow CSV/PDF exports of campaign tables with influencer info, attribute ratings, qualitative notes, and total scores.

## Data Model Implications

- **Users**: authentication plus role flags (analyst, strategist, viewer).
- **Campaigns**: metadata (name, client, market, objective, created_by).
- **KOL Sources**: publish links, platform, CreatorIQ id, scrape status/result payload.
- **Influencers**: canonical profile info (name, handle, tier, demo stats).
- **CampaignInfluencer**: join table storing campaign context, attribute components, qualitative notes, computed attribute + total scores, and audit timestamps.
- **Dashboard Snapshots**: optional materialized summaries for fast display/export.

