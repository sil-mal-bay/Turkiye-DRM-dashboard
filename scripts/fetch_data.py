#!/usr/bin/env python3
"""
Türkiye Disaster Risk Management Dashboard — Main Data Pipeline

Fetches earthquake, flood, hazard, news, video, event, learning, and alert data
from public APIs and RSS feeds. Designed to run daily via GitHub Actions at
07:30 Turkey time (UTC+3).

Usage:
    python scripts/fetch_data.py
"""

import json
import os
import re
import time
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Turkey bounding box (lon_min, lon_max, lat_min, lat_max)
TR_BBOX = {"minlon": 25.5, "maxlon": 45.0, "minlat": 35.5, "maxlat": 42.5}

# Retry settings
MAX_RETRIES = 2
RETRY_DELAY = 5  # seconds

# Shared HTTP session
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "TurkiyeDRM-Dashboard/1.0"})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def fetch_url(url: str, **kwargs) -> requests.Response:
    """GET a URL with retries."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = SESSION.get(url, timeout=30, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            if attempt < MAX_RETRIES:
                print(f"  [retry {attempt + 1}/{MAX_RETRIES}] {exc}")
                time.sleep(RETRY_DELAY)
            else:
                raise


def save_json(filename: str, data: dict) -> None:
    """Write data dict to DATA_DIR/<filename>."""
    path = DATA_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  -> Saved {path}")


def parse_feed(url: str) -> feedparser.FeedParserDict:
    """Fetch and parse an RSS/Atom feed with retries."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            feed = feedparser.parse(url, agent="TurkiyeDRM-Dashboard/1.0")
            if feed.bozo and not feed.entries:
                raise ValueError(f"Feed parse error: {feed.bozo_exception}")
            return feed
        except Exception as exc:
            if attempt < MAX_RETRIES:
                print(f"  [retry {attempt + 1}/{MAX_RETRIES}] feed error: {exc}")
                time.sleep(RETRY_DELAY)
            else:
                raise


def translate_with_haiku(text: str) -> str:
    """Translate Turkish text to English using Claude Haiku.

    Returns the original text unchanged if the API key is missing or the call
    fails.
    """
    if not ANTHROPIC_API_KEY or not text:
        return text
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-20250414",
            max_tokens=256,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Translate the following Turkish text to English. "
                        "Return ONLY the English translation, nothing else.\n\n"
                        f"{text}"
                    ),
                }
            ],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        print(f"  [haiku translation failed: {exc}]")
        return text


# ---------------------------------------------------------------------------
# 1. Earthquakes — USGS + Kandilli
# ---------------------------------------------------------------------------


def fetch_earthquakes() -> None:
    print("\n=== 1. Earthquakes ===")

    events = []
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")

    # --- USGS FDSNWS ---
    print("  Fetching USGS FDSNWS …")
    usgs_url = (
        "https://earthquake.usgs.gov/fdsnws/event/1/query?"
        f"format=geojson&starttime={start}"
        f"&minlatitude={TR_BBOX['minlat']}&maxlatitude={TR_BBOX['maxlat']}"
        f"&minlongitude={TR_BBOX['minlon']}&maxlongitude={TR_BBOX['maxlon']}"
        "&minmagnitude=3.0&orderby=magnitude"
    )
    try:
        resp = fetch_url(usgs_url)
        data = resp.json()
        for feat in data.get("features", []):
            props = feat["properties"]
            coords = feat["geometry"]["coordinates"]
            events.append({
                "source": "USGS",
                "magnitude": props.get("mag"),
                "place": props.get("place"),
                "time": datetime.fromtimestamp(
                    props["time"] / 1000, tz=timezone.utc
                ).isoformat(),
                "latitude": coords[1],
                "longitude": coords[0],
                "depth_km": coords[2],
                "url": props.get("url"),
            })
        print(f"  USGS: {len(events)} events")
    except Exception as exc:
        print(f"  USGS fetch failed: {exc}")

    # --- Kandilli Observatory scrape ---
    print("  Scraping Kandilli Observatory …")
    kandilli_events = []
    try:
        resp = fetch_url("http://www.koeri.boun.edu.tr/scripts/lst0.asp")
        soup = BeautifulSoup(resp.content, "html.parser")
        pre = soup.find("pre")
        if pre:
            lines = pre.get_text().splitlines()
            for line in lines:
                line = line.strip()
                if not line or line.startswith("---") or line.startswith("Date"):
                    continue
                # Kandilli format: Date Time Lat Lon Depth MD ML Mw Region
                parts = line.split()
                if len(parts) < 9:
                    continue
                try:
                    date_str = parts[0]  # yyyy.mm.dd
                    time_str = parts[1]  # hh:mm:ss
                    lat = float(parts[2])
                    lon = float(parts[3])
                    depth = float(parts[4])
                    # ML is typically in column index 6
                    ml = float(parts[6]) if parts[6] != "-.-" else None
                    region = " ".join(parts[8:]).strip("()")
                    if ml is not None and ml >= 3.0:
                        dt_str = f"{date_str.replace('.', '-')}T{time_str}+00:00"
                        kandilli_events.append({
                            "source": "Kandilli",
                            "magnitude": ml,
                            "place": region,
                            "time": dt_str,
                            "latitude": lat,
                            "longitude": lon,
                            "depth_km": depth,
                            "url": "http://www.koeri.boun.edu.tr/scripts/lst0.asp",
                        })
                except (ValueError, IndexError):
                    continue
        print(f"  Kandilli: {len(kandilli_events)} events (M3.0+)")
    except Exception as exc:
        print(f"  Kandilli scrape failed: {exc}")

    # Merge — USGS is authoritative; add Kandilli events not already covered
    usgs_times = {e["time"][:16] for e in events}  # match to the minute
    for ke in kandilli_events:
        if ke["time"][:16] not in usgs_times:
            events.append(ke)

    # Sort by magnitude descending
    events.sort(key=lambda e: e.get("magnitude") or 0, reverse=True)

    largest = events[0] if events else None
    significant = [e for e in events if (e.get("magnitude") or 0) >= 4.0]

    output = {
        "last_updated": now_iso(),
        "count": len(events),
        "largest": largest,
        "significant_events": significant,
        "events": events,
    }
    save_json("earthquakes.json", output)


# ---------------------------------------------------------------------------
# 2. Flood Warnings — GDACS RSS
# ---------------------------------------------------------------------------


def fetch_floods() -> None:
    print("\n=== 2. Flood Warnings (GDACS) ===")

    warnings = []
    try:
        feed = parse_feed("https://www.gdacs.org/xml/rss.xml")
        turkey_patterns = re.compile(
            r"\bturkey\b|\btürkiye\b|\bturkiye\b", re.IGNORECASE
        )
        for entry in feed.entries:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            combined = f"{title} {summary}"
            is_flood = "flood" in combined.lower() or "sel" in combined.lower()
            is_turkey = turkey_patterns.search(combined)
            if is_flood and is_turkey:
                published = entry.get("published", "")
                warnings.append({
                    "title": title,
                    "description": summary,
                    "published": published,
                    "link": entry.get("link", ""),
                    "severity": _extract_gdacs_severity(entry),
                    "region": _extract_region(title, summary),
                })
        print(f"  Found {len(warnings)} flood warning(s) for Turkey")
    except Exception as exc:
        print(f"  GDACS flood fetch failed: {exc}")

    output = {
        "last_updated": now_iso(),
        "count": len(warnings),
        "warnings": warnings,
    }
    save_json("floods.json", output)


def _extract_gdacs_severity(entry) -> str:
    """Try to pull severity/alert level from GDACS entry."""
    for attr in ("gdacs_alertlevel", "gdacs:alertlevel"):
        val = entry.get(attr)
        if val:
            return val
    # Fall back to looking in the title
    title_lower = entry.get("title", "").lower()
    for level in ("red", "orange", "green"):
        if level in title_lower:
            return level.capitalize()
    return "Unknown"


def _extract_region(title: str, summary: str) -> str:
    """Best-effort region extraction from title/summary text."""
    # Look for common Turkish region names or just return the title
    combined = f"{title} {summary}"
    region_patterns = [
        r"in\s+([A-ZÇĞİÖŞÜa-zçğıöşü\s]+?)(?:\s*,|\s*-|\s*\.|\s*$)",
    ]
    for pat in region_patterns:
        match = re.search(pat, combined)
        if match:
            return match.group(1).strip()
    return title


# ---------------------------------------------------------------------------
# 3. Other Hazards — GDACS (landslides, drought, fire)
# ---------------------------------------------------------------------------


def fetch_other_hazards() -> None:
    print("\n=== 3. Other Hazards (GDACS) ===")

    hazards = []
    turkey_patterns = re.compile(
        r"\bturkey\b|\btürkiye\b|\bturkiye\b", re.IGNORECASE
    )
    hazard_keywords = re.compile(
        r"landslide|drought|fire|wildfire|volcan|storm|cyclone"
        r"|heyelan|kuraklık|yangın",
        re.IGNORECASE,
    )

    try:
        feed = parse_feed("https://www.gdacs.org/xml/rss.xml")
        for entry in feed.entries:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            combined = f"{title} {summary}"
            if turkey_patterns.search(combined) and hazard_keywords.search(combined):
                # Determine hazard type
                htype = "Other"
                lower = combined.lower()
                for keyword, label in [
                    ("landslide", "Landslide"),
                    ("heyelan", "Landslide"),
                    ("drought", "Drought"),
                    ("kuraklık", "Drought"),
                    ("fire", "Wildfire"),
                    ("wildfire", "Wildfire"),
                    ("yangın", "Wildfire"),
                    ("storm", "Storm"),
                    ("cyclone", "Storm"),
                    ("volcan", "Volcanic"),
                ]:
                    if keyword in lower:
                        htype = label
                        break

                hazards.append({
                    "type": htype,
                    "title": title,
                    "description": summary,
                    "published": entry.get("published", ""),
                    "link": entry.get("link", ""),
                    "severity": _extract_gdacs_severity(entry),
                })
        print(f"  Found {len(hazards)} other hazard(s) for Turkey")
    except Exception as exc:
        print(f"  GDACS other-hazards fetch failed: {exc}")

    output = {
        "last_updated": now_iso(),
        "count": len(hazards),
        "hazards": hazards,
    }
    save_json("other_hazards.json", output)


# ---------------------------------------------------------------------------
# 4. News Digest — RSS feeds + scoring + dedup + Haiku translation
# ---------------------------------------------------------------------------

# Credibility scores by source key
CREDIBILITY = {
    "reuters": 95,
    "ap": 95,
    "afp": 95,
    "aljazeera": 90,
    "bbc": 90,
    "undrr": 90,
    "anadolu": 85,
    "dailysabah": 80,
    "hurriyet": 80,
    "trt": 75,
    "reliefweb": 65,
}

NEWS_FEEDS = [
    # --- DRM-specific (reliable DRM content) ---
    ("undrr", "https://www.undrr.org/rss.xml"),
    # --- General news (may carry Turkey/disaster stories) ---
    ("aljazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("anadolu", "https://www.aa.com.tr/en/rss/default?cat=turkey"),
    ("dailysabah", "https://www.dailysabah.com/rssFeed/turkey"),
    ("hurriyet", "https://www.hurriyetdailynews.com/rss"),
    ("bbc", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("reliefweb", "https://reliefweb.int/updates/rss.xml"),
]

# Hard filter keywords — SPECIFIC disaster/hazard types only.
# Uses a compiled regex with word boundaries to prevent substring false
# positives (e.g. Turkish "sel" matching inside "baseless").
DISASTER_PATTERN = re.compile(
    r"\b(?:"
    # English
    r"earthquake|flood|wildfire|forest\s+fire|drought"
    r"|landslide|mudslide|tsunami|seismic"
    # Turkish
    r"|deprem|sel|orman\s+yang[ıi]n[ıi]|kurakl[ıi]k"
    r"|heyelan|[çc]amur\s+akmas[ıi]|sismik"
    r")\b",
    re.IGNORECASE,
)

# General DRM phrases — articles whose title/lead explicitly discusses
# disaster risk management as a field pass the filter without needing to
# mention a specific hazard type.
GENERAL_DRM_PATTERN = re.compile(
    r"\b(?:"
    r"disaster\s+risk\s+(?:management|reduction|finance)"
    r"|disaster\s+(?:preparedness|resilience|management)"
    r"|natural\s+hazards?"
    r"|early\s+warning\s+systems?"
    r"|climate\s+resilience"
    r"|afet\s+(?:riski|y[öo]netimi)"
    r")\b",
    re.IGNORECASE,
)

# DRM keywords — focused on mitigation, preparation, infrastructure,
# and best practices for disaster risk reduction.  An article must mention
# at least one of these (in title or summary) to pass the general filter.
# The Turkey fast-pass rule bypasses this check.
DRM_KEYWORDS = {
    # Mitigation & prevention
    "mitigation", "mitigate", "prevention", "preventive", "protect",
    "risk reduction", "risk management", "disaster risk",
    # Preparedness
    "preparedness", "preparation", "preparing", "contingency",
    "early warning", "evacuation plan",
    # Infrastructure & building
    "infrastructure", "retrofit", "retrofitting", "building code",
    "seismic design", "structural reinforcement", "construction standard",
    "earthquake-resistant", "flood-resistant", "firebreak",
    "urban planning", "land use planning", "zoning",
    "building safety", "shelter design",
    # Resilience & adaptation
    "resilience", "resilient", "adaptation", "climate adaptation",
    "capacity building", "vulnerability assessment",
    # Reconstruction
    "reconstruction", "rebuild", "recovery plan",
    # Policy & best practices
    "best practice", "guideline", "disaster management",
    "emergency management", "hazard mapping", "risk assessment",
    # Key acronyms
    "drr", "drm", "gfdrr",
    # Turkish equivalents
    "afet yönetimi", "risk azaltma", "dayanıklılık", "depreme dayanıklı",
    "yapı güvenliği", "erken uyarı", "altyapı", "imar planı",
}

TURKEY_PATTERN = re.compile(
    r"\bturkey\b|\btürkiye\b|\bturkiye\b", re.IGNORECASE
)

WORLD_BANK_PATTERN = re.compile(
    r"\bworld\s+bank\b|\bdünya\s+bankası\b|\bgfdrr\b|\bifc\b", re.IGNORECASE
)

# English stopwords for dedup
STOPWORDS = set(
    "a an the and or but in on at to for of is it this that with from by as "
    "are was were be been has have had do does did will would shall should may "
    "might can could about after before between through during".split()
)


def _recency_score(published_dt: datetime) -> int:
    """Map article age to a recency score."""
    now = datetime.now(timezone.utc)
    age = now - published_dt
    hours = age.total_seconds() / 3600
    if hours < 12:
        return 100
    elif hours < 24:
        return 90
    elif hours < 48:
        return 75
    elif hours < 72:
        return 55
    elif hours < 96:
        return 35
    elif hours < 120:
        return 15
    else:
        return 0


def _parse_entry_time(entry) -> datetime | None:
    """Extract a timezone-aware datetime from a feed entry."""
    for field in ("published_parsed", "updated_parsed"):
        tp = entry.get(field)
        if tp:
            try:
                from calendar import timegm
                return datetime.fromtimestamp(timegm(tp), tz=timezone.utc)
            except Exception:
                continue
    # Try parsing date string directly
    for field in ("published", "updated"):
        ds = entry.get(field)
        if ds:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(ds)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                continue
    return None


def _title_keywords(title: str) -> set:
    """Extract non-stopword lowercase tokens from a title."""
    tokens = re.findall(r"[a-zA-ZçğıöşüÇĞİÖŞÜ]+", title.lower())
    return {t for t in tokens if t not in STOPWORDS and len(t) > 2}


def _is_turkish(text: str) -> bool:
    """Heuristic: does the text contain Turkish-specific characters?"""
    turkish_chars = set("çğıöşüÇĞİÖŞÜ")
    return any(ch in turkish_chars for ch in text)


def fetch_news() -> None:
    print("\n=== 4. News Digest ===")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=14)
    articles = []

    for source_key, feed_url in NEWS_FEEDS:
        print(f"  Fetching {source_key} …")
        try:
            feed = parse_feed(feed_url)
            for entry in feed.entries:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                title_lower = title.lower()

                # Strip HTML tags from summary and truncate to first 500
                # chars so we match on the article's lead content, not on
                # keywords buried in 10K-word ReliefWeb HTML dumps.
                clean_summary = re.sub(r"<[^>]+>", " ", summary)
                clean_summary = re.sub(r"\s+", " ", clean_summary).strip()[:500]
                combined = f"{title} {clean_summary}".lower()

                # --- FILTERING ---
                # Three paths to inclusion:
                #
                # PATH A (Turkey fast-pass): headline mentions Turkey +
                #   a specific disaster → auto include.
                #
                # PATH B (hazard + DRM): article lead mentions a specific
                #   hazard AND a DRM/mitigation keyword.
                #
                # PATH C (general DRM): article lead explicitly discusses
                #   disaster risk management as a field (e.g. "disaster risk
                #   reduction", "disaster preparedness") even without naming
                #   a specific hazard.

                title_has_turkey = TURKEY_PATTERN.search(title) is not None
                title_has_disaster = DISASTER_PATTERN.search(title) is not None
                fast_pass = title_has_turkey and title_has_disaster  # PATH A

                if not fast_pass:
                    has_disaster = DISASTER_PATTERN.search(combined) is not None
                    has_drm = any(kw in combined for kw in DRM_KEYWORDS)
                    has_general_drm = GENERAL_DRM_PATTERN.search(combined) is not None

                    path_b = has_disaster and has_drm
                    path_c = has_general_drm

                    if not (path_b or path_c):
                        continue

                # Parse time and enforce 5-day cutoff
                pub_dt = _parse_entry_time(entry)
                if pub_dt is None:
                    continue
                if pub_dt < cutoff:
                    # Exception: World Bank articles get minimum 5-day display
                    if not WORLD_BANK_PATTERN.search(combined):
                        continue

                # Translate Turkish titles/descriptions
                translated_title = title
                translated_summary = summary
                if _is_turkish(title):
                    translated_title = translate_with_haiku(title)
                if _is_turkish(summary):
                    translated_summary = translate_with_haiku(summary)

                # Scoring
                cred = CREDIBILITY.get(source_key, 70)
                rec = _recency_score(pub_dt)
                score = 0.35 * cred + 0.65 * rec
                wb_boost = WORLD_BANK_PATTERN.search(combined) is not None
                if wb_boost:
                    score += 25

                # Turkey boost: 2x if article mentions Turkey anywhere
                mentions_turkey = TURKEY_PATTERN.search(combined) is not None
                if mentions_turkey:
                    score *= 2.0

                articles.append({
                    "source": source_key,
                    "title": translated_title,
                    "original_title": title if translated_title != title else None,
                    "description": translated_summary,
                    "published": pub_dt.isoformat(),
                    "link": entry.get("link", ""),
                    "score": round(score, 2),
                    "world_bank_related": wb_boost,
                    "turkey_related": mentions_turkey,
                    "credibility_score": cred,
                    "recency_score": rec,
                })
            print(f"    -> {source_key}: collected candidates")
        except Exception as exc:
            print(f"    -> {source_key} failed: {exc}")

    # ------------------------------------------------------------------
    # Deduplication: keyword overlap in title within 12-hour window
    # TODO: Upgrade to Claude Sonnet embeddings-based dedup
    # ------------------------------------------------------------------
    articles.sort(key=lambda a: a["score"], reverse=True)
    deduped = []
    for art in articles:
        kw = _title_keywords(art["title"])
        art_time = datetime.fromisoformat(art["published"])
        is_dup = False
        for kept in deduped:
            kept_kw = _title_keywords(kept["title"])
            kept_time = datetime.fromisoformat(kept["published"])
            time_diff = abs((art_time - kept_time).total_seconds()) / 3600
            overlap = kw & kept_kw
            if len(overlap) >= 3 and time_diff <= 12:
                is_dup = True
                break
        if not is_dup:
            deduped.append(art)

    print(f"  Total after dedup: {len(deduped)} articles (from {len(articles)} candidates)")

    output = {
        "last_updated": now_iso(),
        "count": len(deduped),
        "articles": deduped,
    }
    save_json("news.json", output)


# ---------------------------------------------------------------------------
# 5. Videos & Webinars — YouTube Data API
# ---------------------------------------------------------------------------

# Official channel IDs — ONLY these channels are queried (no keyword searches)
YOUTUBE_CHANNELS = {
    "GFDRR": "UCueMQfh8JiMWJR3JDhVHMEg",
    "World Bank": "UCz_l26KhGrPMPlJiCqhR0sA",
    "UNDRR": "UCbMfHSvgqGaOal7K3sFPOXg",
    "World Bank Live": "UCi0bHy8BGVr8s7FCrEl03HA",
    "UNDP": "UCbeaB2MYxLhWoRY9tBUTFOA",
}

# Videos must mention at least one of these keywords in title or description
VIDEO_RELEVANCE_KEYWORDS = re.compile(
    r"earthquake|flood|disaster|resilience|risk|drm|seismic|infrastructure"
    r"|reconstruction|climate|hazard|türkiye|turkey|deprem|sel|afet",
    re.IGNORECASE,
)


def fetch_videos() -> None:
    print("\n=== 5. Videos & Webinars (YouTube) ===")

    if not YOUTUBE_API_KEY:
        print("  YOUTUBE_API_KEY not set — skipping video fetch")
        save_json("videos.json", {
            "last_updated": now_iso(),
            "count": 0,
            "videos": [],
            "note": "YOUTUBE_API_KEY not configured",
        })
        return

    videos = []
    seen_ids = set()

    def _add_video(item, from_official_channel=False):
        """Add a video.  Skip keyword filter for official channels."""
        vid_id = None
        if isinstance(item.get("id"), dict):
            vid_id = item["id"].get("videoId")
        elif isinstance(item.get("id"), str):
            vid_id = item["id"]
        # Also check contentDetails for playlist items
        if not vid_id:
            vid_id = item.get("contentDetails", {}).get("videoId")
        if not vid_id or vid_id in seen_ids:
            return
        snippet = item.get("snippet", {})
        title = snippet.get("title") or ""
        description = (snippet.get("description") or "")[:300]
        # Trust official channel uploads entirely — only filter search results
        if not from_official_channel:
            combined = f"{title} {description}"
            if not VIDEO_RELEVANCE_KEYWORDS.search(combined):
                return
        seen_ids.add(vid_id)
        videos.append({
            "video_id": vid_id,
            "title": title,
            "description": description,
            "channel": snippet.get("channelTitle"),
            "published": snippet.get("publishedAt"),
            "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url"),
            "url": f"https://www.youtube.com/watch?v={vid_id}",
        })

    # Fetch uploads from each official channel — take up to 15 most recent,
    # NO keyword filter (these are trusted DRM channels).
    MAX_PER_CHANNEL = 15
    for name, channel_id in YOUTUBE_CHANNELS.items():
        print(f"  Fetching videos from {name} channel …")
        try:
            # Get the uploads playlist ID for this channel
            ch_url = (
                "https://www.googleapis.com/youtube/v3/channels?"
                f"part=contentDetails&id={channel_id}&key={YOUTUBE_API_KEY}"
            )
            ch_resp = fetch_url(ch_url).json()
            # Check for API errors
            if "error" in ch_resp:
                err_msg = ch_resp["error"].get("message", "unknown error")
                print(f"    {name}: API error — {err_msg}")
                continue
            items = ch_resp.get("items", [])
            if not items:
                print(f"    {name}: no channel data returned (check channel ID)")
                continue
            uploads_id = (
                items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
            )
            # Fetch recent uploads
            pl_url = (
                "https://www.googleapis.com/youtube/v3/playlistItems?"
                f"part=snippet,contentDetails&playlistId={uploads_id}"
                f"&maxResults={MAX_PER_CHANNEL}&key={YOUTUBE_API_KEY}"
            )
            pl_resp = fetch_url(pl_url).json()
            if "error" in pl_resp:
                err_msg = pl_resp["error"].get("message", "unknown error")
                print(f"    {name}: playlist API error — {err_msg}")
                continue
            before = len(videos)
            for item in pl_resp.get("items", []):
                _add_video(item, from_official_channel=True)
            added = len(videos) - before
            print(f"    {name}: {added} videos added")
        except Exception as exc:
            print(f"    {name} channel failed: {exc}")

    # Sort newest first (no expiry — keep all)
    videos.sort(
        key=lambda v: v.get("published") or "1970-01-01", reverse=True
    )
    print(f"  Total videos: {len(videos)}")

    output = {
        "last_updated": now_iso(),
        "count": len(videos),
        "videos": videos,
    }
    save_json("videos.json", output)


# ---------------------------------------------------------------------------
# 6. Upcoming Events — PreventionWeb + GFDRR
# ---------------------------------------------------------------------------


# Regex to detect date-like strings that GFDRR returns as titles
_DATE_TITLE_PATTERN = re.compile(
    r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}"
    r"(?:\s*[-–]\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2})?"
    r",?\s*\d{4}$",
    re.IGNORECASE,
)


def _parse_event_date(text: str):
    """Try to extract a date from free-form text. Returns datetime or None."""
    from dateutil import parser as dateutil_parser
    # Try several patterns
    patterns = [
        # "17 November 2025", "2 Feb 2026"
        r"(\d{1,2}\s+\w+\s+\d{4})",
        # "November 17, 2025"
        r"(\w+\s+\d{1,2},?\s+\d{4})",
        # "2025-11-17"
        r"(\d{4}-\d{2}-\d{2})",
    ]
    for pat in patterns:
        match = re.search(pat, text)
        if match:
            try:
                dt = dateutil_parser.parse(match.group(1), fuzzy=True)
                return dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
    return None


def _detect_event_type(title: str) -> str:
    """Best-effort event type classification from title text."""
    lower = title.lower()
    if "webinar" in lower:
        return "webinar"
    elif "workshop" in lower:
        return "workshop"
    elif "conference" in lower or "summit" in lower or "forum" in lower:
        return "conference"
    elif "training" in lower or "course" in lower or "learning" in lower:
        return "training"
    elif "seminar" in lower:
        return "seminar"
    return "event"


def fetch_events() -> None:
    print("\n=== 6. Upcoming Events ===")

    events = []
    seen_links = set()
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=60)

    def _add_event(source, title, link, date_dt=None, location=None, event_type=None):
        """De-duplicate and add event, skipping date-like titles."""
        if not title or not link:
            return
        title = title.strip()[:200]
        if _DATE_TITLE_PATTERN.match(title):
            return
        if link in seen_links:
            return
        seen_links.add(link)
        events.append({
            "source": source,
            "title": title,
            "date": date_dt.isoformat() if date_dt else None,
            "link": link,
            "location": location,
            "type": event_type or _detect_event_type(title),
        })

    # --- PRIMARY: GFDRR events page (Drupal, server-rendered HTML) ---
    print("  Scraping GFDRR events …")
    gfdrr_count = 0
    try:
        resp = fetch_url("https://www.gfdrr.org/en/events")
        soup = BeautifulSoup(resp.text, "html.parser")
        for row in soup.select(".views-row"):
            # Date field: .views-field-field-date-1 a
            date_el = row.select_one(
                ".views-field-field-date-1 a, .views-field-field-date a"
            )
            date_text = date_el.get_text(strip=True) if date_el else ""
            event_date = _parse_event_date(date_text) if date_text else None

            # Title field: .views-field-title a  or  .views-field-nothing a
            title_el = row.select_one(
                ".views-field-title a, .views-field-nothing a"
            )
            if not title_el:
                # Fallback: first non-date link in the row
                for a_tag in row.find_all("a", href=True):
                    candidate = a_tag.get_text(strip=True)
                    if candidate and not _DATE_TITLE_PATTERN.match(candidate):
                        title_el = a_tag
                        break
            if not title_el:
                continue

            raw_title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            link = (
                href
                if href.startswith("http")
                else f"https://www.gfdrr.org{href}"
            )

            # Title often includes "| Location" — split it
            location = None
            if "|" in raw_title:
                parts = raw_title.split("|", 1)
                raw_title = parts[0].strip()
                location = parts[1].strip().rstrip(",")

            if raw_title and link:
                _add_event("GFDRR", raw_title, link, event_date, location)
                gfdrr_count += 1
        print(f"    GFDRR: {gfdrr_count} events")
    except Exception as exc:
        print(f"    GFDRR events scrape failed: {exc}")

    # --- PRIMARY: UNDRR events page (Drupal, server-rendered HTML) ---
    print("  Scraping UNDRR events …")
    undrr_count = 0
    try:
        resp = fetch_url("https://www.undrr.org/events")
        soup = BeautifulSoup(resp.text, "html.parser")
        for row in soup.select(".views-row"):
            # Title: header.mg-card__title a (link contains /event/)
            title_el = row.select_one(
                "header.mg-card__title a, .mg-card__title a"
            )
            if not title_el:
                # Fallback: any link with /event/ in href
                title_el = row.find("a", href=lambda h: h and "/event/" in h)
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            link = (
                href
                if href.startswith("http")
                else f"https://www.undrr.org{href}"
            )

            # Date: field--name-field-event-date-range (Drupal field)
            date_el = row.select_one(
                ".field--name-field-event-date-range, .mg-card__date"
            )
            event_date = None
            if date_el:
                event_date = _parse_event_date(date_el.get_text(strip=True))

            if title and link:
                _add_event("UNDRR", title, link, event_date)
                undrr_count += 1
        print(f"    UNDRR: {undrr_count} events")
    except Exception as exc:
        print(f"    UNDRR events scrape failed: {exc}")

    # --- SECONDARY: PreventionWeb RSS (may return 403) ---
    print("  Trying PreventionWeb events RSS …")
    pw_count = 0
    try:
        feed = parse_feed("https://www.preventionweb.net/rss/drr-events.xml")
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "")
            date_dt = _parse_entry_time(entry) or _parse_event_date(
                entry.get("summary", "") + " " + entry.get("title", "")
            )
            _add_event("PreventionWeb", title, link, date_dt)
            pw_count += 1
        print(f"    PreventionWeb RSS: {pw_count} entries")
    except Exception as exc:
        print(f"    PreventionWeb RSS failed: {exc}")

    # --- FALLBACK: ReliefWeb Training API (may require registered appname) ---
    print("  Trying ReliefWeb training events (fallback) …")
    rw_count = 0
    try:
        rw_url = (
            "https://api.reliefweb.int/v1/training?"
            "appname=turkiye-drm"
            "&limit=50"
            "&sort[]=date.start:asc"
            "&filter[operator]=AND"
            "&filter[conditions][0][field]=date.start"
            "&filter[conditions][0][value][from]=now"
            "&filter[conditions][1][field]=date.start"
            "&filter[conditions][1][value][to]=now%2B60d"
            "&fields[include][]=title"
            "&fields[include][]=url"
            "&fields[include][]=date"
            "&fields[include][]=source"
            "&fields[include][]=country"
            "&fields[include][]=format"
        )
        resp = fetch_url(rw_url)
        rw_data = resp.json()
        for item in rw_data.get("data", []):
            fields = item.get("fields", {})
            title = fields.get("title", "")
            link = fields.get("url", "") or item.get("href", "")
            date_info = fields.get("date", {})
            date_start = ""
            if isinstance(date_info, dict):
                date_start = date_info.get("start", "") or date_info.get("created", "")
            date_dt = None
            if date_start:
                try:
                    date_dt = datetime.fromisoformat(
                        date_start.replace("Z", "+00:00")
                    )
                except Exception:
                    pass
            location = None
            countries = fields.get("country", [])
            if countries and isinstance(countries, list):
                names = [
                    c.get("name", "") if isinstance(c, dict) else str(c)
                    for c in countries
                ]
                location = ", ".join(n for n in names if n)
            event_type = None
            formats = fields.get("format", [])
            if formats and isinstance(formats, list):
                fmt = formats[0]
                if isinstance(fmt, dict):
                    event_type = fmt.get("name", "").lower() or None
            sources = fields.get("source", [])
            source_name = "ReliefWeb"
            if sources and isinstance(sources, list) and isinstance(sources[0], dict):
                source_name = sources[0].get("name", "ReliefWeb")
            if title:
                _add_event(source_name, title, link, date_dt, location, event_type)
                rw_count += 1
        print(f"    ReliefWeb training: {rw_count} events")
    except Exception as exc:
        print(f"    ReliefWeb training failed: {exc}")

    # Filter to next 60 days where date is known, keep undated ones too
    filtered = []
    for ev in events:
        if ev["date"]:
            try:
                ev_dt = datetime.fromisoformat(ev["date"])
                if now <= ev_dt <= horizon:
                    filtered.append(ev)
            except Exception:
                filtered.append(ev)
        else:
            filtered.append(ev)

    # Sort by date (soonest first); undated events go to the end
    filtered.sort(key=lambda e: e.get("date") or "9999-12-31")

    print(f"  Total events (next 60 days): {len(filtered)}")

    output = {
        "last_updated": now_iso(),
        "count": len(filtered),
        "events": filtered,
    }
    save_json("events.json", output)


# ---------------------------------------------------------------------------
# 7. Learning Materials — World Bank Academy + UNDRR GETI
# ---------------------------------------------------------------------------


def fetch_learning() -> None:
    print("\n=== 7. Learning Materials ===")

    materials = []
    seen_links = set()

    def _add_material(source, title, link, mat_type="course", description=""):
        """De-duplicate and add a learning material."""
        if not title or not link:
            return
        if link in seen_links:
            return
        seen_links.add(link)
        materials.append({
            "source": source,
            "title": title.strip()[:200],
            "link": link,
            "type": mat_type,
            "description": description[:300] if description else "",
        })

    # --- PRIMARY: World Bank Documents API v3 ---
    # Search for DRM training/learning documents
    WB_LEARNING_QUERIES = [
        "disaster risk management training",
        "disaster risk reduction course",
        "earthquake resilience building code",
        "flood risk management infrastructure",
    ]
    print("  Fetching World Bank DRM documents …")
    wb_count = 0
    for qterm in WB_LEARNING_QUERIES:
        try:
            wb_url = (
                "https://search.worldbank.org/api/v3/wds?"
                f"format=json&qterm={qterm.replace(' ', '+')}"
                "&rows=10&os=0&srt=ddate_desc"
            )
            resp = fetch_url(wb_url)
            wb_data = resp.json()
            for doc_key, doc in wb_data.get("documents", {}).items():
                if not isinstance(doc, dict):
                    continue
                title = doc.get("display_title", "") or doc.get("docna", "")
                if isinstance(title, dict):
                    title = title.get("0", {}).get("docna", "")
                link = doc.get("url", "") or doc.get("pdfurl", "")
                if not link:
                    # Build link from doc ID
                    doc_id = doc.get("id", "")
                    if doc_id:
                        link = (
                            "https://documents.worldbank.org/en/publication/"
                            f"documents-reports/documentdetail/{doc_id}"
                        )
                desc = doc.get("abstracts", "") or ""
                if isinstance(desc, dict):
                    desc = desc.get("cdata", "")
                doc_type = (doc.get("docty", "") or "").lower()
                mat_type = "report"
                if "toolkit" in doc_type:
                    mat_type = "toolkit"
                elif "brief" in doc_type or "note" in doc_type:
                    mat_type = "brief"
                elif "working paper" in doc_type:
                    mat_type = "working paper"
                if title:
                    _add_material("World Bank", title, link, mat_type, desc)
                    wb_count += 1
        except Exception as exc:
            print(f"    WB query '{qterm}' failed: {exc}")
    print(f"    World Bank documents: {wb_count} items")

    # --- PRIMARY: GFDRR publications page (Drupal, server-rendered) ---
    print("  Scraping GFDRR publications …")
    gfdrr_count = 0
    try:
        resp = fetch_url("https://www.gfdrr.org/en/publications")
        soup = BeautifulSoup(resp.text, "html.parser")
        for item in soup.select(".views-row")[:25]:
            # Find the first link with actual text (skip image wrappers)
            title = ""
            link = ""
            for a_tag in item.find_all("a", href=True):
                text = a_tag.get_text(strip=True)
                if text and not _DATE_TITLE_PATTERN.match(text):
                    title = text
                    href = a_tag.get("href", "")
                    link = (
                        href
                        if href.startswith("http")
                        else f"https://www.gfdrr.org{href}"
                    )
                    break
            if title and link:
                _add_material("GFDRR", title, link, "report")
                gfdrr_count += 1
        print(f"    GFDRR publications: {gfdrr_count} items")
    except Exception as exc:
        print(f"    GFDRR publications scrape failed: {exc}")

    # --- SECONDARY: UNDRR publications (Drupal, server-rendered) ---
    print("  Scraping UNDRR publications …")
    undrr_count = 0
    try:
        resp = fetch_url("https://www.undrr.org/publications")
        soup = BeautifulSoup(resp.text, "html.parser")
        for row in soup.select(".views-row")[:25]:
            title_el = row.select_one(
                "header.mg-card__title a, .mg-card__title a"
            )
            if not title_el:
                title_el = row.find("a", href=True)
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            link = (
                href
                if href.startswith("http")
                else f"https://www.undrr.org{href}"
            )
            if title and not _DATE_TITLE_PATTERN.match(title):
                _add_material("UNDRR", title, link, "learning")
                undrr_count += 1
        print(f"    UNDRR publications: {undrr_count} items")
    except Exception as exc:
        print(f"    UNDRR publications scrape failed: {exc}")

    print(f"  Total learning materials: {len(materials)}")

    output = {
        "last_updated": now_iso(),
        "count": len(materials),
        "items": materials,
    }
    save_json("learning.json", output)


# ---------------------------------------------------------------------------
# 8. Publications — ReliefWeb + GFDRR + UNDRR + World Bank Documents
# ---------------------------------------------------------------------------


def fetch_publications() -> None:
    print("\n=== 8. Publications ===")

    publications = []
    seen_links = set()

    def _add_pub(source, title, description, date_str, link, pub_type="report"):
        """De-duplicate and add a publication entry."""
        if not title or not link:
            return
        if link in seen_links:
            return
        seen_links.add(link)
        publications.append({
            "source": source,
            "title": title.strip()[:300],
            "description": (description or "").strip()[:500],
            "date": date_str,
            "link": link,
            "type": pub_type,
        })

    # --- ReliefWeb Reports API ---
    print("  Fetching ReliefWeb reports …")
    rw_count = 0
    try:
        rw_url = (
            "https://api.reliefweb.int/v1/reports?"
            "appname=turkiye-drm"
            "&filter[operator]=AND"
            "&filter[conditions][0][field]=primary_country.name"
            "&filter[conditions][0][value]=Turkey"
            "&filter[conditions][1][field]=date.created"
            "&filter[conditions][1][value][from]=now-30d"
            "&limit=20&sort[]=date:desc"
            "&fields[include][]=title"
            "&fields[include][]=url"
            "&fields[include][]=date"
            "&fields[include][]=source"
            "&fields[include][]=format"
        )
        resp = fetch_url(rw_url)
        rw_data = resp.json()
        for item in rw_data.get("data", []):
            fields = item.get("fields", {})
            title = fields.get("title", "")
            link = fields.get("url", "") or item.get("href", "")
            date_info = fields.get("date", {})
            date_str = date_info.get("created", "") if isinstance(date_info, dict) else ""
            # Determine type from format
            formats = fields.get("format", [])
            pub_type = "report"
            if formats:
                fmt_name = ""
                if isinstance(formats, list) and formats:
                    fmt_name = formats[0].get("name", "").lower() if isinstance(formats[0], dict) else str(formats[0]).lower()
                if "brief" in fmt_name or "note" in fmt_name:
                    pub_type = "brief"
                elif "situation" in fmt_name:
                    pub_type = "report"
                elif "assessment" in fmt_name:
                    pub_type = "report"
            # Source names
            sources = fields.get("source", [])
            source_name = "ReliefWeb"
            if sources and isinstance(sources, list) and isinstance(sources[0], dict):
                source_name = sources[0].get("name", "ReliefWeb")
            _add_pub(source_name, title, "", date_str, link, pub_type)
            rw_count += 1
        print(f"    ReliefWeb reports: {rw_count} publications")
    except Exception as exc:
        print(f"    ReliefWeb reports fetch failed: {exc}")

    # --- GFDRR Publications page ---
    print("  Scraping GFDRR publications …")
    gfdrr_count = 0
    gfdrr_urls = [
        "https://www.gfdrr.org/en/publications",
        "https://www.gfdrr.org/rss.xml",
    ]
    for gfdrr_url in gfdrr_urls:
        if gfdrr_count > 0:
            break  # first URL worked, skip fallback
        try:
            if gfdrr_url.endswith(".xml"):
                # RSS fallback
                feed = parse_feed(gfdrr_url)
                for entry in feed.entries[:30]:
                    title = entry.get("title", "").strip()
                    link = entry.get("link", "")
                    desc = entry.get("summary", "")[:500]
                    date_dt = _parse_entry_time(entry)
                    date_str = date_dt.isoformat() if date_dt else ""
                    if title and not _DATE_TITLE_PATTERN.match(title):
                        _add_pub("GFDRR", title, desc, date_str, link, "report")
                        gfdrr_count += 1
                print(f"    GFDRR RSS: {gfdrr_count} publications")
            else:
                resp = fetch_url(gfdrr_url)
                soup = BeautifulSoup(resp.text, "html.parser")
                items = soup.select(
                    "article, .views-row, .node--type-publication, .publication-item, .card"
                )
                for item in items[:30]:
                    link_tag = item.find("a", href=True)
                    if not link_tag:
                        continue
                    title = link_tag.get_text(strip=True)
                    href = link_tag.get("href", "")
                    link = (
                        href
                        if href.startswith("http")
                        else f"https://www.gfdrr.org{href}"
                    )
                    desc = ""
                    desc_tag = item.find(["p", ".field--name-body", ".summary"])
                    if desc_tag:
                        desc = desc_tag.get_text(strip=True)[:500]
                    date_dt = _parse_event_date(item.get_text())
                    date_str = date_dt.isoformat() if date_dt else ""
                    if title and not _DATE_TITLE_PATTERN.match(title):
                        _add_pub("GFDRR", title, desc, date_str, link, "report")
                        gfdrr_count += 1
                print(f"    GFDRR page scrape: {gfdrr_count} publications")
        except Exception as exc:
            print(f"    GFDRR ({gfdrr_url}) failed: {exc}")

    # --- UNDRR Publications page ---
    print("  Scraping UNDRR publications …")
    undrr_count = 0
    undrr_pub_urls = [
        ("https://www.undrr.org/publications", "undrr.org"),
        ("https://www.preventionweb.net/publications", "preventionweb.net"),
    ]
    for undrr_url, domain in undrr_pub_urls:
        try:
            resp = fetch_url(undrr_url)
            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.select(
                "article, .views-row, .node--type-publication, .card, .publication-item"
            )
            base = f"https://www.{domain}"
            for item in items[:30]:
                link_tag = item.find("a", href=True)
                if not link_tag:
                    continue
                title = link_tag.get_text(strip=True)
                href = link_tag.get("href", "")
                link = (
                    href
                    if href.startswith("http")
                    else f"{base}{href}"
                )
                desc = ""
                desc_tag = item.find(["p", ".field--name-body", ".summary"])
                if desc_tag:
                    desc = desc_tag.get_text(strip=True)[:500]
                date_dt = _parse_event_date(item.get_text())
                date_str = date_dt.isoformat() if date_dt else ""
                source_label = "UNDRR" if "undrr" in domain else "PreventionWeb"
                if title and not _DATE_TITLE_PATTERN.match(title):
                    _add_pub(source_label, title, desc, date_str, link, "report")
                    undrr_count += 1
            print(f"    {domain} publications: {undrr_count}")
        except Exception as exc:
            print(f"    {domain} publications scrape failed: {exc}")

    # --- World Bank Documents Search API ---
    print("  Fetching World Bank documents …")
    wb_count = 0
    wb_cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d")
    # Title substrings to skip (procurement / safeguards noise)
    WB_SKIP_TITLES = {"procurement plan", "safeguards data sheet"}
    # Acceptable document types (lowercase substrings)
    WB_KEEP_TYPES = {"report", "brief", "working paper", "evaluation", "note", "toolkit"}
    try:
        wb_url = (
            "https://search.worldbank.org/api/v2/wds?"
            "format=json&qterm=Turkey+disaster+risk&rows=20&os=0"
            "&srt=ddate_desc"
        )
        resp = fetch_url(wb_url)
        wb_data = resp.json()
        docs = wb_data.get("documents", {})
        for doc_key, doc in docs.items():
            if not isinstance(doc, dict):
                continue
            title = doc.get("display_title", "") or doc.get("doctitle", "")
            link = doc.get("url", "") or doc.get("pdfurl", "")
            date_str = doc.get("docdt", "") or doc.get("disclosure_date", "")

            # Skip documents older than 12 months
            if date_str and date_str[:10] < wb_cutoff:
                continue

            # Skip procurement plans and safeguards data sheets
            title_lower = title.lower()
            if any(skip in title_lower for skip in WB_SKIP_TITLES):
                continue

            # Only keep acceptable document types
            doc_type_raw = (doc.get("doctype", "") or "").lower()
            if doc_type_raw and not any(kt in doc_type_raw for kt in WB_KEEP_TYPES):
                continue

            desc = doc.get("abstracts", "") or ""
            if isinstance(desc, dict):
                desc = desc.get("cdata", "")
            pub_type = "report"
            if "brief" in doc_type_raw or "note" in doc_type_raw:
                pub_type = "brief"
            elif "working paper" in doc_type_raw:
                pub_type = "working paper"
            elif "evaluation" in doc_type_raw:
                pub_type = "evaluation"
            elif "toolkit" in doc_type_raw:
                pub_type = "toolkit"
            if title:
                _add_pub("World Bank", title, desc[:500], date_str, link, pub_type)
                wb_count += 1
        print(f"    World Bank documents: {wb_count}")
    except Exception as exc:
        print(f"    World Bank documents fetch failed: {exc}")

    # Sort by date (newest first); undated at the end
    publications.sort(
        key=lambda p: p.get("date") or "0000-00-00", reverse=True
    )

    print(f"  Total publications: {len(publications)}")

    output = {
        "last_updated": now_iso(),
        "count": len(publications),
        "publications": publications,
    }
    save_json("publications.json", output)


# ---------------------------------------------------------------------------
# 9. Active Alerts — derived from earthquakes + floods
# ---------------------------------------------------------------------------


def fetch_alerts() -> None:
    print("\n=== 9. Active Alerts ===")

    alerts = []
    now = datetime.now(timezone.utc)
    cutoff_48h = now - timedelta(hours=48)

    # --- Earthquake alerts: M4.0+ in last 48 hours ---
    eq_path = DATA_DIR / "earthquakes.json"
    if eq_path.exists():
        with open(eq_path, "r", encoding="utf-8") as f:
            eq_data = json.load(f)
        for ev in eq_data.get("events", []):
            try:
                ev_time = datetime.fromisoformat(ev["time"])
                mag = ev.get("magnitude") or 0
                if mag >= 4.0 and ev_time >= cutoff_48h:
                    alerts.append({
                        "type": "earthquake",
                        "severity": "high" if mag >= 5.0 else "medium",
                        "title": f"M{mag} earthquake — {ev.get('place', 'Turkey')}",
                        "magnitude": mag,
                        "time": ev["time"],
                        "location": ev.get("place"),
                        "latitude": ev.get("latitude"),
                        "longitude": ev.get("longitude"),
                        "source": ev.get("source"),
                        "url": ev.get("url"),
                    })
            except Exception:
                continue
        print(f"  Earthquake alerts: {len([a for a in alerts if a['type'] == 'earthquake'])}")
    else:
        print("  earthquakes.json not found — skipping earthquake alerts")

    # --- Flood alerts: medium+ severity in last 48 hours ---
    fl_path = DATA_DIR / "floods.json"
    if fl_path.exists():
        with open(fl_path, "r", encoding="utf-8") as f:
            fl_data = json.load(f)
        medium_plus = {"red", "orange", "medium", "high"}
        for w in fl_data.get("warnings", []):
            sev = (w.get("severity") or "").lower()
            if sev in medium_plus:
                # Check recency if published date is available
                try:
                    from email.utils import parsedate_to_datetime
                    pub = parsedate_to_datetime(w["published"])
                    if pub.tzinfo is None:
                        pub = pub.replace(tzinfo=timezone.utc)
                    if pub < cutoff_48h:
                        continue
                except Exception:
                    pass  # If we can't parse the date, include it anyway
                alerts.append({
                    "type": "flood",
                    "severity": sev,
                    "title": w.get("title", "Flood warning — Turkey"),
                    "time": w.get("published"),
                    "region": w.get("region"),
                    "link": w.get("link"),
                })
        flood_alerts = [a for a in alerts if a["type"] == "flood"]
        print(f"  Flood alerts: {len(flood_alerts)}")
    else:
        print("  floods.json not found — skipping flood alerts")

    # Sort by severity then time
    severity_order = {"high": 0, "red": 0, "orange": 1, "medium": 1, "low": 2, "green": 2}
    alerts.sort(key=lambda a: (
        severity_order.get(a.get("severity", "").lower(), 9),
        a.get("time") or "",
    ))

    print(f"  Total active alerts: {len(alerts)}")

    output = {
        "last_updated": now_iso(),
        "count": len(alerts),
        "alerts": alerts,
    }
    save_json("alerts.json", output)


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------


def main():
    start = time.time()
    print("=" * 60)
    print("Türkiye DRM Dashboard — Data Pipeline")
    print(f"Started: {now_iso()}")
    print("=" * 60)

    steps = [
        ("Earthquakes", fetch_earthquakes),
        ("Floods", fetch_floods),
        ("Other Hazards", fetch_other_hazards),
        ("News Digest", fetch_news),
        ("Videos", fetch_videos),
        ("Events", fetch_events),
        ("Learning", fetch_learning),
        ("Publications", fetch_publications),
        ("Alerts", fetch_alerts),  # Must run after earthquakes + floods
    ]

    results = {}
    for name, func in steps:
        try:
            func()
            results[name] = "OK"
        except Exception as exc:
            print(f"\n  *** {name} FAILED: {exc} ***")
            results[name] = f"FAILED: {exc}"

    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print("Pipeline Summary")
    print("=" * 60)
    for name, status in results.items():
        symbol = "+" if status == "OK" else "!"
        print(f"  [{symbol}] {name}: {status}")
    print(f"\nCompleted in {elapsed:.1f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
