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
    "anadolu": 85,
    "dailysabah": 80,
    "hurriyet": 80,
    "trt": 75,
    "reliefweb": 65,
}

NEWS_FEEDS = [
    ("reuters", "https://www.reutersagency.com/feed/?taxonomy=best-regions&post_type=best"),
    ("aljazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("anadolu", "https://www.aa.com.tr/en/rss/default?cat=turkey"),
    ("dailysabah", "https://www.dailysabah.com/rssFeed/turkey"),
    ("hurriyet", "https://www.hurriyetdailynews.com/rss"),
    ("bbc", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("trt", "https://www.trtworld.com/rss"),
    ("reliefweb", "https://reliefweb.int/updates/rss.xml"),
]

# Hard filter keywords
DISASTER_KEYWORDS_EN = {
    "earthquake", "flood", "forest fire", "wildfire", "drought",
    "landslide", "mudslide",
}
DISASTER_KEYWORDS_TR = {
    "deprem", "sel", "orman yangını", "kuraklık", "heyelan", "çamur akması",
}
ALL_DISASTER_KEYWORDS = DISASTER_KEYWORDS_EN | DISASTER_KEYWORDS_TR

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
    cutoff = now - timedelta(days=5)
    articles = []

    for source_key, feed_url in NEWS_FEEDS:
        print(f"  Fetching {source_key} …")
        try:
            feed = parse_feed(feed_url)
            for entry in feed.entries:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                combined = f"{title} {summary}".lower()

                # Hard filter: must mention Turkey
                if not TURKEY_PATTERN.search(combined):
                    continue

                # Hard filter: must mention at least one disaster keyword
                has_disaster = any(kw in combined for kw in ALL_DISASTER_KEYWORDS)
                if not has_disaster:
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

                articles.append({
                    "source": source_key,
                    "title": translated_title,
                    "original_title": title if translated_title != title else None,
                    "description": translated_summary,
                    "published": pub_dt.isoformat(),
                    "link": entry.get("link", ""),
                    "score": round(score, 2),
                    "world_bank_related": wb_boost,
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

# Known channel IDs (as of 2025)
YOUTUBE_CHANNELS = {
    "GFDRR": "UCueMQfh8JiMWJR3JDhVHMEg",
    "World Bank": "UCz_l26KhGrPMPlJiCqhR0sA",
    "UNDRR": "UCbMfHSvgqGaOal7K3sFPOXg",
}

YOUTUBE_SEARCH_QUERIES = [
    "Turkey earthquake disaster",
    "Türkiye deprem",
    "Turkey flood disaster risk",
]


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

    def _add_video(item):
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
        seen_ids.add(vid_id)
        snippet = item.get("snippet", {})
        videos.append({
            "video_id": vid_id,
            "title": snippet.get("title"),
            "description": (snippet.get("description") or "")[:300],
            "channel": snippet.get("channelTitle"),
            "published": snippet.get("publishedAt"),
            "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url"),
            "url": f"https://www.youtube.com/watch?v={vid_id}",
        })

    # Search by channel uploads
    for name, channel_id in YOUTUBE_CHANNELS.items():
        print(f"  Fetching videos from {name} channel …")
        try:
            # Get uploads playlist
            ch_url = (
                "https://www.googleapis.com/youtube/v3/channels?"
                f"part=contentDetails&id={channel_id}&key={YOUTUBE_API_KEY}"
            )
            ch_resp = fetch_url(ch_url).json()
            items = ch_resp.get("items", [])
            if not items:
                continue
            uploads_id = (
                items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
            )
            pl_url = (
                "https://www.googleapis.com/youtube/v3/playlistItems?"
                f"part=snippet,contentDetails&playlistId={uploads_id}"
                f"&maxResults=10&key={YOUTUBE_API_KEY}"
            )
            pl_resp = fetch_url(pl_url).json()
            for item in pl_resp.get("items", []):
                _add_video(item)
        except Exception as exc:
            print(f"    {name} channel failed: {exc}")

    # Keyword search
    for query in YOUTUBE_SEARCH_QUERIES:
        print(f"  Searching YouTube: '{query}' …")
        try:
            s_url = (
                "https://www.googleapis.com/youtube/v3/search?"
                f"part=snippet&q={quote_plus(query)}"
                f"&type=video&maxResults=10&order=date&key={YOUTUBE_API_KEY}"
            )
            s_resp = fetch_url(s_url).json()
            for item in s_resp.get("items", []):
                _add_video(item)
        except Exception as exc:
            print(f"    Search '{query}' failed: {exc}")

    # Sort newest first
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


def fetch_events() -> None:
    print("\n=== 6. Upcoming Events ===")

    events = []
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=60)

    # --- PreventionWeb ---
    print("  Scraping PreventionWeb events …")
    try:
        resp = fetch_url("https://www.preventionweb.net/events")
        soup = BeautifulSoup(resp.text, "html.parser")
        # PreventionWeb uses structured event cards
        event_cards = soup.select("article, .event-card, .views-row, .event-item")
        for card in event_cards:
            link_tag = card.find("a", href=True)
            title = card.get_text(strip=True)[:200]
            link = ""
            if link_tag:
                href = link_tag.get("href", "")
                title = link_tag.get_text(strip=True) or title
                link = href if href.startswith("http") else f"https://www.preventionweb.net{href}"

            # Try to find dates in the card text
            date_match = re.search(
                r"(\d{1,2}\s+\w+\s+\d{4})", card.get_text()
            )
            event_date = None
            if date_match:
                try:
                    from dateutil import parser as dateutil_parser
                    event_date = dateutil_parser.parse(
                        date_match.group(1), fuzzy=True
                    ).replace(tzinfo=timezone.utc)
                except Exception:
                    pass

            if title and link:
                events.append({
                    "source": "PreventionWeb",
                    "title": title[:200],
                    "date": event_date.isoformat() if event_date else None,
                    "link": link,
                })
        print(f"    PreventionWeb: {len(events)} events scraped")
    except Exception as exc:
        print(f"    PreventionWeb scrape failed: {exc}")

    # --- GFDRR events ---
    print("  Scraping GFDRR events …")
    gfdrr_count = 0
    try:
        resp = fetch_url("https://www.gfdrr.org/en/events")
        soup = BeautifulSoup(resp.text, "html.parser")
        event_items = soup.select("article, .views-row, .event-item, .node--type-event")
        for item in event_items:
            link_tag = item.find("a", href=True)
            title = ""
            link = ""
            if link_tag:
                title = link_tag.get_text(strip=True)
                href = link_tag.get("href", "")
                link = href if href.startswith("http") else f"https://www.gfdrr.org{href}"

            date_match = re.search(
                r"(\d{1,2}\s+\w+\s+\d{4})", item.get_text()
            )
            event_date = None
            if date_match:
                try:
                    from dateutil import parser as dateutil_parser
                    event_date = dateutil_parser.parse(
                        date_match.group(1), fuzzy=True
                    ).replace(tzinfo=timezone.utc)
                except Exception:
                    pass

            if title and link:
                events.append({
                    "source": "GFDRR",
                    "title": title[:200],
                    "date": event_date.isoformat() if event_date else None,
                    "link": link,
                })
                gfdrr_count += 1
        print(f"    GFDRR: {gfdrr_count} events scraped")
    except Exception as exc:
        print(f"    GFDRR scrape failed: {exc}")

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

    # --- World Bank Open Learning Campus / Urban Resilience ---
    print("  Scraping World Bank learning resources …")
    try:
        resp = fetch_url(
            "https://olc.worldbank.org/search?search_api_fulltext=urban+resilience+disaster+risk"
        )
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select(".views-row, .search-result, article")
        for item in items[:20]:  # cap at 20
            link_tag = item.find("a", href=True)
            if link_tag:
                title = link_tag.get_text(strip=True)
                href = link_tag["href"]
                link = href if href.startswith("http") else f"https://olc.worldbank.org{href}"
                if title:
                    materials.append({
                        "source": "World Bank OLC",
                        "title": title[:200],
                        "link": link,
                        "type": "course",
                    })
        print(f"    World Bank OLC: {len(materials)} items")
    except Exception as exc:
        print(f"    World Bank OLC scrape failed: {exc}")

    # --- UNDRR GETI (Global Education and Training Institute) ---
    print("  Scraping UNDRR GETI …")
    geti_count = 0
    try:
        resp = fetch_url("https://www.undrr.org/learning")
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select(".views-row, article, .node--type-learning, .card")
        for item in items[:20]:
            link_tag = item.find("a", href=True)
            if link_tag:
                title = link_tag.get_text(strip=True)
                href = link_tag["href"]
                link = href if href.startswith("http") else f"https://www.undrr.org{href}"
                if title:
                    materials.append({
                        "source": "UNDRR",
                        "title": title[:200],
                        "link": link,
                        "type": "learning",
                    })
                    geti_count += 1
        print(f"    UNDRR GETI: {geti_count} items")
    except Exception as exc:
        print(f"    UNDRR GETI scrape failed: {exc}")

    print(f"  Total learning materials: {len(materials)}")

    output = {
        "last_updated": now_iso(),
        "count": len(materials),
        "materials": materials,
    }
    save_json("learning.json", output)


# ---------------------------------------------------------------------------
# 8. Active Alerts — derived from earthquakes + floods
# ---------------------------------------------------------------------------


def fetch_alerts() -> None:
    print("\n=== 8. Active Alerts ===")

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
