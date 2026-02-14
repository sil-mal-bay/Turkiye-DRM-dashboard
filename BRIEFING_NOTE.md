# Turkiye Disaster Risk Management Dashboard -- Briefing Note

**Date:** February 14, 2026
**Prepared by:** Alex Panetta (project lead)
**Prepared for:** World Bank Turkey Disaster Management Team

---

## Project Overview

This project is a web dashboard built for the World Bank's disaster management operations in Turkiye. It aggregates hazard data, news, publications, videos, events, and learning materials into a single briefing-style page. The dashboard is hosted on GitHub Pages and its underlying data is refreshed daily via a GitHub Actions pipeline.

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
│   ├── news.json               Filtered + ranked news articles
│   ├── videos.json             YouTube videos from partner channels
│   ├── events.json             Upcoming DRM events
│   ├── learning.json           World Bank Academy + UNDRR GETI courses
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
- **GDACS:** Landslide alerts.
- **Daily scraping:** Mudslides, drought conditions, forest fires in Turkey. Free, no API key required.

### News
- **RSS feeds from:** Reuters, Al Jazeera, Anadolu Agency, Daily Sabah, Hurriyet Daily News, BBC, TRT World, ReliefWeb. All free, no API keys required.

### Videos
- **YouTube Data API v3:** Pulls videos from GFDRR, UNDRR, and World Bank channels. **Requires YOUTUBE_API_KEY** (Google Cloud). Free tier provides 10,000 quota units/day, which is more than sufficient.

### Events
- **PreventionWeb** and **GFDRR event listings:** Scraped for upcoming events within a 60-day forward window. Free, no API key required.

### Learning Materials
- **World Bank Academy** and **UNDRR GETI:** Scraped for relevant courses and training materials. Free, no API key required.

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

### Hard Filter
An article must pass both conditions to be included:
1. Mentions **Turkey** or **Turkiye** (including Turkish-language equivalents)
2. Mentions at least one **disaster keyword**: earthquake, flood, forest fire, wildfire, drought, landslide, mudslide -- plus Turkish equivalents of each

### Time Cutoff
- Articles older than **5 days** are dropped entirely.

### Ranking Formula
Each article receives a composite score:

```
score = 0.35 x credibility + 0.65 x recency + world_bank_boost
```

### Credibility Tiers

| Tier | Sources | Score |
|---|---|---|
| Tier 1 | Reuters, AP, AFP | 95 |
| Tier 2 | Al Jazeera, BBC | 90 |
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

### World Bank Boost
- Articles mentioning **World Bank**, **GFDRR**, **IFC**, or **Dunya Bankasi** receive a **+25 point boost** and a **minimum 5-day display** period (they will not be dropped before 5 days even if they would otherwise fall off).

### Deduplication
- If two articles share **3 or more non-stopword title terms** and were published within **12 hours** of each other, only the higher-scoring article is kept.
- **Claude Sonnet** is used for deduplication judgment calls (semantic similarity).
- **Claude Haiku** is used for all other Claude tasks (translation, etc.).

---

## Translation

- **Claude Haiku** handles Turkish-to-English translation of article titles and descriptions.
- **Claude Sonnet** is reserved for deduplication decisions only (where semantic judgment is needed).
- Estimated Claude API cost: approximately **$3--4 per month**.

---

## Content Lifecycle Rules

Each content type has distinct display and expiration rules:

| Content Type | Window | Sort Order | Expiration |
|---|---|---|---|
| Earthquakes | Rolling 7-day window | Most recent first | Drops after 7 days. Only M3.0+ from Kandilli. |
| Active Alerts | 48 hours | Most recent first | M4.0+ earthquakes or medium+ flood warnings within 48 hours. |
| News | 5-day window | Ranked by credibility + recency formula | Drops after 5 days (except World Bank-boosted articles). |
| Videos / Webinars | Never expire | Newest first | Homepage shows 3--4; rest accessible via "View all" link. |
| Upcoming Events | 60-day forward window | Closest date first | Removed after the event date passes. |
| Past Events | N/A | N/A | Eliminated entirely unless a video recording exists, in which case the recording moves to the Videos section. |
| Learning Materials | Refreshed weekly | Upcoming start dates first | Prioritize Turkey-relevant content and courses with upcoming start dates. |

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

### Source Color Coding
| Source | Color |
|---|---|
| GFDRR | Blue |
| UNDRR | Green |
| World Bank Academy | Amber |

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
| `anthropic` (>=0.40.0) | Claude API client (translation, dedup) |
| `python-dotenv` (>=1.0.0) | Loading `.env` file for local development |
| `beautifulsoup4` (>=4.12.0) | HTML parsing for web scraping |

---

## Monthly Costs

| Item | Cost |
|---|---|
| GitHub Actions (CI/CD) | Free (well within 2,000 minutes/month limit) |
| GitHub Pages (hosting) | Free |
| USGS, GDACS, RSS, Kandilli (data) | Free |
| YouTube Data API v3 | Free (10,000 quota units/day) |
| Claude API (translation + dedup) | ~$3--4/month |
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
