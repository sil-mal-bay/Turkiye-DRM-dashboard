# Turkiye Disaster Risk Management Dashboard -- Briefing Note

**Date:** February 15, 2026
**Prepared by:** Alex Panetta (project lead)
**Prepared for:** World Bank Turkey Disaster Management Team

---

## Project Overview

This project is a web dashboard built for the World Bank's disaster management operations in Turkiye. It aggregates hazard data, news, publications, videos, events, and learning materials into a single briefing-style page. The dashboard is hosted on GitHub Pages and its underlying data is refreshed daily via a GitHub Actions pipeline.

**Live Dashboard:** https://sil-mal-bay.github.io/Turkiye-DRM-dashboard/

**GitHub Repository:** https://github.com/sil-mal-bay/Turkiye-DRM-dashboard (private)

**Team:**
- **Alex Panetta** -- project lead
- **Silvia** -- GitHub repo owner, dashboard operator

---

## Architecture

The system has two components:

1. **Frontend:** A static HTML file (`index.html`) that reads from JSON data files in the `data/` directory. Hosted for free on GitHub Pages.
2. **Backend:** A Python script (`scripts/fetch_data.py`) that fetches, filters, translates, deduplicates, and writes data into JSON files. Runs daily at 7:30 AM Turkey time (04:30 UTC) via GitHub Actions. Also supports manual triggering via `workflow_dispatch`.

There is no server, no database, and no backend runtime. The Python script runs as a GitHub Actions job, commits updated JSON files to the repo, and GitHub Pages serves the static site.

---

## File Structure

```
Turkiye-DRM-dashboard/
├── index.html                  Dashboard frontend (static HTML)
├── diri-fay.jpg                Turkey fault line map (background image)
├── scripts/
│   └── fetch_data.py           Main data pipeline (Python)
├── data/
│   ├── earthquakes.json        USGS + Kandilli earthquake data
│   ├── floods.json             GDACS flood warnings
│   ├── other_hazards.json      Landslides, mudslides, drought, forest fires
│   ├── news.json               Filtered + ranked DRM news articles
│   ├── videos.json             YouTube videos from partner channels
│   ├── events.json             Upcoming DRM events (GFDRR + UNDRR)
│   ├── learning.json           World Bank + GFDRR + UNDRR learning materials
│   ├── publications.json       UNDRR publications + reports
│   └── alerts.json             Active high-priority alerts
├── .github/
│   └── workflows/
│       └── update.yml          GitHub Actions workflow (cron + manual)
├── .env.example                Template for local development API keys
├── .gitignore                  Excludes .env, __pycache__, venv, .DS_Store
├── requirements.txt            Python dependencies
└── BRIEFING_NOTE.md            This file
```

---

## Data Sources

All data sources are free and require no authentication unless noted.

### Earthquakes
- **USGS FDSNWS API:** Magnitude 3.0+ earthquakes within the Turkey bounding box. Free, no API key required.
- **Kandilli Observatory:** Scraped for Turkey-specific seismic data. Free, no API key required.

### Flood Warnings
- **GDACS RSS feed:** Filtered for events in Turkey. Free, no API key required.

### Other Hazards
- **GDACS:** Landslide, drought, and wildfire alerts filtered for Turkey.

### Hazard Cards (frontend)
- Earthquake, flood, and other hazard stat cards are **clickable links** to their respective source monitoring pages (USGS earthquake map, GDACS flood merge, GDACS main dashboard).

### News
- **UNDRR RSS** (`undrr.org/rss.xml`): Primary DRM-specific source. Provides disaster risk management, resilience, and preparedness content. Free, no API key required.
- **General news RSS feeds:** Al Jazeera, Anadolu Agency, Daily Sabah, Hurriyet Daily News, BBC, ReliefWeb. These carry Turkey/disaster stories during active events. All free, no API keys required.

### Videos
- **YouTube Data API v3:** Pulls videos from GFDRR, UNDRR, World Bank, World Bank Live, and UNDP channels. Official channel uploads bypass keyword filtering (trusted source). Search results are filtered for DRM relevance. **Requires YOUTUBE_API_KEY** (Google Cloud). Free tier provides 10,000 quota units/day.

### Events
- **GFDRR events page** (`gfdrr.org/en/events`): Scraped (Drupal server-rendered HTML). CSS selectors: `.views-row`, `.views-field-field-date-1`, `.views-field-title`.
- **UNDRR events page** (`undrr.org/events`): Scraped (Drupal server-rendered HTML). CSS selectors: `.views-row`, `.field--name-field-event-date-range`, `header.mg-card__title a`.
- **PreventionWeb RSS** (fallback): `preventionweb.net/rss/drr-events.xml` -- currently returning 403.
- **ReliefWeb Training API** (fallback): Requires registered appname (since Nov 2025) -- currently returning 400.

### Learning Materials
- **World Bank Documents API v3** (`search.worldbank.org/api/v3/wds`): Queries for DRM-related documents using multiple search terms (disaster risk management, earthquake resilience, flood preparedness, etc.). Filtered to last 2 years. Free, no API key required. Typically returns ~40 items.
- **GFDRR publications page** (`gfdrr.org/en/publications`): Scraped for publication titles and links. Drupal HTML with `.views-row` selectors. Typically returns ~12 items.
- **UNDRR publications page** (`undrr.org/publications`): Scraped using `header.mg-card__title a` selectors. Typically returns ~10 items.

### Publications
- **UNDRR publications** (primary): Scraped from `undrr.org/publications`. Returns ~20 items.
- **ReliefWeb Reports API** (currently failing -- needs registered appname).
- **GFDRR RSS** (currently returning malformed XML).

---

## API Keys Required

Two API keys are needed. Both are stored as GitHub repository secrets under **Settings > Secrets and variables > Actions**.

| Secret Name | Source | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic (Claude API) | Translation (Turkish to English) and deduplication |
| `YOUTUBE_API_KEY` | Google Cloud Console | Fetching videos from YouTube Data API v3 |

For local development, copy `.env.example` to `.env` and fill in the values. The `.env` file is gitignored and must never be committed.

---

## News Filtering Logic

### Three-Path Filter

An article must pass at least one of three paths to be included:

**Path A -- Turkey Fast-Pass:** The article headline mentions **Turkey/Turkiye** AND a specific **disaster type** (earthquake, flood, wildfire, forest fire, drought, landslide, mudslide, tsunami). This path auto-includes articles about disasters occurring in Turkey without requiring DRM-specific language.

**Path B -- Hazard + DRM:** The article's title and lead (first 500 characters of description, HTML stripped) mentions a specific **disaster type** AND at least one **DRM keyword** from the mitigation/preparedness/infrastructure/resilience vocabulary.

**Path C -- General DRM:** The article's title and lead contains an explicit **DRM phrase** such as "disaster risk management", "disaster risk reduction", "disaster preparedness", "disaster resilience", "natural hazard(s)", "early warning system(s)", or "climate resilience". This path allows articles about DRM as a field even without naming a specific hazard.

### Disaster Types (regex with word boundaries)
English: earthquake, flood, wildfire, forest fire, drought, landslide, mudslide, tsunami, seismic
Turkish: deprem, sel, orman yangini, kuraklik, heyelan, camur akmasi, sismik

Word boundaries (`\b`) prevent substring false positives (e.g., Turkish "sel" matching inside "baseless").

### DRM Keywords (Path B vocabulary)
Mitigation, prevention, risk reduction, risk management, disaster risk, preparedness, early warning, evacuation plan, infrastructure, retrofit, building code, seismic design, structural reinforcement, earthquake-resistant, flood-resistant, urban planning, resilience, adaptation, capacity building, vulnerability assessment, reconstruction, recovery plan, best practice, guideline, disaster management, emergency management, hazard mapping, risk assessment, DRR, DRM, GFDRR, plus Turkish equivalents.

### Time Cutoff
- Articles older than **14 days** are dropped. DRM content moves slower than breaking news; a 14-day window ensures the dashboard always has content even during quiet periods.

### Turkey Boost
- Articles mentioning **Turkey/Turkiye** anywhere in title or lead receive a **2x score multiplier**. This ensures Turkey-specific content rises to the top without excluding valuable global DRM articles.

### Ranking Formula
Each article receives a composite score:

```
score = 0.35 x credibility + 0.65 x recency + world_bank_boost
if mentions_turkey: score *= 2.0
```

### Credibility Tiers

| Tier | Sources | Score |
|---|---|---|
| Tier 1 | Reuters, AP, AFP | 95 |
| Tier 2 | Al Jazeera, BBC, UNDRR | 90 |
| Tier 3 | Anadolu Agency | 85 |
| Tier 4 | Daily Sabah, Hurriyet Daily News | 80 |
| Tier 5 | TRT World | 75 |
| Tier 6 | ReliefWeb | 65 |

### Recency Scoring

| Age | Score |
|---|---|
| Less than 12 hours | 100 |
| 12--24 hours | 90 |
| 1--2 days | 75 |
| 2--3 days | 55 |
| 3--4 days | 35 |
| 4--5 days | 15 |
| 5+ days | 0 |

### World Bank Boost
- Articles mentioning **World Bank**, **GFDRR**, **IFC**, or **Dunya Bankasi** receive a **+25 point boost**.

### Deduplication
- If two articles share **3 or more non-stopword title terms** and were published within **12 hours** of each other, only the higher-scoring article is kept.

### HTML Sanitization
- Article descriptions are stripped of HTML tags and truncated to the first **500 characters** before keyword matching. This prevents false positives from massive HTML dumps (common in ReliefWeb RSS entries).

---

## Translation

- **Claude Haiku** handles Turkish-to-English translation of article titles and descriptions.
- Estimated Claude API cost: approximately **$3--4 per month**.

---

## Content Lifecycle Rules

Each content type has distinct display and expiration rules:

| Content Type | Window | Sort Order | Expiration |
|---|---|---|---|
| Earthquakes | Rolling 7-day window | Magnitude descending | Drops after 7 days. Only M3.0+ from Kandilli. |
| Active Alerts | 48 hours | Most recent first | M4.0+ earthquakes or medium+ flood warnings within 48 hours. |
| News | 14-day window | Ranked by credibility + recency + Turkey boost | Drops after 14 days. Turkey articles boosted 2x. |
| Videos / Webinars | Never expire | Newest first | Official channel uploads bypass keyword filter. |
| Upcoming Events | 60-day forward window | Closest date first | Removed after the event date passes. Undated events shown at end. |
| Learning Materials | Refreshed daily | By source (World Bank, GFDRR, UNDRR) | 2-year lookback for World Bank documents. |
| Publications | Refreshed daily | By date | UNDRR publications (primary source). |

---

## Design Specifications

The dashboard uses a layout internally called **Option B ("Briefing Page")**.

### Layout
- **Two-column layout:** Main content on the left, news sidebar on the right (newspaper-style).
- Responsive for standard desktop viewing.

### Visual Elements
- **Background image:** Turkey fault line map (`diri-fay.jpg`) displayed at **45% opacity**.
- **Header banner:** Navy color (`#1e293b`) with white title text.
- **Cards:** Frosted glass effect using `rgba` white backgrounds with `backdrop-filter: blur()`.
- **Hazard stat cards:** Clickable, linking to source monitoring pages (USGS, GDACS).

### Source Color Coding
| Source | Color |
|---|---|
| GFDRR | Blue |
| UNDRR | Green |
| World Bank Academy | Amber |

---

## Known Issues & Broken Feeds

The following external sources are currently non-functional:

| Source | Issue | Impact |
|---|---|---|
| Reuters RSS | Returns malformed XML | No Reuters articles. Low impact (rarely carries DRM content). |
| TRT World RSS | Returns HTML instead of XML | No TRT articles. Low impact. |
| Anadolu Agency RSS | Returns 0 entries | No Anadolu articles during quiet periods. Works during active Turkey events. |
| Hurriyet Daily News RSS | Returns 0 entries | Same as Anadolu. |
| PreventionWeb | Returns 403 Forbidden | No PreventionWeb events or publications. |
| ReliefWeb API | Returns 400 (requires registered appname since Nov 2025) | No ReliefWeb reports or training events via API. RSS feed still works. |
| GFDRR RSS | Returns malformed XML | No GFDRR publications via RSS (page scraping works). |
| World Bank OLC | JS-rendered (EdCast platform) | Cannot scrape courses. WB Documents API used as alternative. |

---

## GitHub Actions Workflow

The workflow is defined in `.github/workflows/update.yml`.

**Schedule:** Runs daily at `04:30 UTC` (7:30 AM Turkey time, UTC+3).

**Manual trigger:** Available via the GitHub Actions tab -- navigate to "Update Dashboard Data" and click "Run workflow".

**What it does:**
1. Checks out the repository
2. Sets up Python 3.11
3. Installs dependencies from `requirements.txt`
4. Runs `scripts/fetch_data.py` with API keys injected from repository secrets
5. Commits any changes to the `data/` directory and pushes to the main branch

**Commit behavior:** The workflow only commits if there are actual changes to the data files. If nothing changed, no commit is created (avoids empty commits).

---

## Python Dependencies

Defined in `requirements.txt`:

| Package | Purpose |
|---|---|
| `requests` (>=2.31.0) | HTTP requests to APIs and web scraping |
| `feedparser` (>=6.0.0) | Parsing RSS feeds (news, GDACS) |
| `anthropic` (>=0.40.0) | Claude API client (translation) |
| `python-dotenv` (>=1.0.0) | Loading `.env` file for local development |
| `beautifulsoup4` (>=4.12.0) | HTML parsing for web scraping |
| `python-dateutil` | Event date parsing (installed as transitive dependency of feedparser) |

---

## Monthly Costs

| Item | Cost |
|---|---|
| GitHub Actions (CI/CD) | Free (well within 2,000 minutes/month limit) |
| GitHub Pages (hosting) | Free |
| USGS, GDACS, RSS, Kandilli (data) | Free |
| YouTube Data API v3 | Free (10,000 quota units/day) |
| Claude API (translation) | ~$3--4/month |
| **Total** | **~$3--4/month** |

---

## How to Rebuild From Scratch

If this project is deleted or needs to be recreated, follow these steps:

### Step 1: Create the Repository
1. Create a new **private** GitHub repository (e.g., `Turkiye-DRM-dashboard`).
2. Clone it locally.

### Step 2: Recreate the File Structure
Recreate every file listed in the File Structure section above. The critical files are:
- `index.html` -- the dashboard frontend
- `scripts/fetch_data.py` -- the data pipeline
- `.github/workflows/update.yml` -- the GitHub Actions workflow
- `requirements.txt` -- Python dependencies
- `.env.example` -- API key template
- `.gitignore` -- file exclusion rules
- `diri-fay.jpg` -- the Turkey fault line map background image

Create the empty `data/` directory (the pipeline will populate the JSON files on first run).

### Step 3: Configure API Keys
1. Go to the GitHub repository **Settings > Secrets and variables > Actions**.
2. Add **ANTHROPIC_API_KEY** with a valid Claude API key from Anthropic.
3. Add **YOUTUBE_API_KEY** with a valid YouTube Data API v3 key from Google Cloud Console.

### Step 4: Enable GitHub Pages
1. Go to **Settings > Pages**.
2. Set the source to the **main branch** (root directory).
3. Save. The site will be available at `https://<username>.github.io/Turkiye-DRM-dashboard/`.

### Step 5: Verify
1. The GitHub Action will run automatically at 7:30 AM Turkey time (04:30 UTC) daily.
2. To trigger a manual run: go to the **Actions** tab, select "Update Dashboard Data", and click **"Run workflow"**.
3. After the first successful run, the `data/` directory will be populated with JSON files and the dashboard will display live data.

### Key Rebuild Notes
- The `diri-fay.jpg` background image must be sourced separately (it is a Turkey active fault line map).
- The `fetch_data.py` script contains all filtering, ranking, translation, and deduplication logic described in this document.
- The `index.html` file contains all frontend layout, styling, and JavaScript for reading the JSON data files.
- Both the Python script and the HTML file are the core intellectual property of this project -- they encode all the business logic.

---

## Reference: GitHub Actions Workflow (update.yml)

```yaml
name: Update Dashboard Data

on:
  schedule:
    # 7:30 AM Turkey time (UTC+3) = 04:30 UTC
    - cron: '30 4 * * *'
  workflow_dispatch:
    # Manual trigger -- click "Run workflow" in GitHub Actions tab

jobs:
  fetch-data:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run data pipeline
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          YOUTUBE_API_KEY: ${{ secrets.YOUTUBE_API_KEY }}
        run: python scripts/fetch_data.py

      - name: Commit and push updated data
        run: |
          git config user.name "GitHub Actions Bot"
          git config user.email "actions@github.com"
          git add data/
          git diff --staged --quiet || git commit -m "Update dashboard data $(date -u +'%Y-%m-%d %H:%M UTC')"
          git push
```

---

## Reference: requirements.txt

```
requests>=2.31.0
feedparser>=6.0.0
anthropic>=0.40.0
python-dotenv>=1.0.0
beautifulsoup4>=4.12.0
```

---

## Reference: .env.example

```
ANTHROPIC_API_KEY=your-claude-api-key-here
YOUTUBE_API_KEY=your-youtube-api-key-here
```

---

## Reference: .gitignore

```
.env
__pycache__/
*.pyc
.DS_Store
venv/
```
