#!/usr/bin/env python3
"""
APP_HORDE build.py — fetch sideloading repo sources, normalize into the site DB,
verify bundle IDs against the App Store, and emit index.html.

Usage:
    python3 build.py [--offline] [--out index.html]

    --offline   reuse sources/*.json snapshots instead of downloading (testing)
    --out       output path (default: index.html next to this script)

Deterministic: identical source snapshots -> byte-identical index.html.
The App Store verification cache is seeded from the previous index.html's DB;
only NEW bundle IDs hit Apple's lookup API (batched, polite).

Outputs also print a DB content hash so the weekly Action can skip no-op commits.
"""
import ast
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES_DIR = os.path.join(HERE, "sources")

# ---------------------------------------------------------------- config ---
SOURCES = {
    "alans":       dict(label="Alan's Gigantic Repo", short="Alan's",
                        url="https://fastsign.dev/repo.json",
                        cls="s-alans", official=False),
    "apptesters":  dict(label="AppTesters IPA Repo", short="AppTesters",
                        url="https://repository.apptesters.org",
                        cls="s-apt", official=False),
    "cypwn":       dict(label="CyPwn IPA Library", short="CyPwn",
                        url="https://ipa.cypwn.xyz/cypwn.json",
                        cls="s-cypwn", official=False),
    "quantum":     dict(label="Quantum Source", short="Quantum",
                        url="https://quarksources.github.io/quantumsource.json",
                        cls="s-qnt", official=False),
    "quantumplus": dict(label="Quantum Plus", short="Quantum+",
                        url="https://quarksources.github.io/quantumsource++.json",
                        cls="s-qntp", official=False),
    "accretion":   dict(label="Accretion", short="Accretion",
                        url="https://getaccretion.github.io/source.json",
                        cls="s-acc", official=False),
    "ipaomtk":     dict(label="IPAOMTK", short="IPAOMTK",
                        url="https://ipaomtk.com",
                        cls="s-mtk", official=False, direct=False),
    "wuxu":        dict(label="WuXu's Library++", short="WuXu's",
                        url="https://wuxu1.github.io/wuxu-complete-plus.json",
                        cls="s-wuxu", official=False),
    "starfiles":   dict(label="Starfiles", short="Starfiles",
                        url="https://repo.starfiles.co/public?gbox",
                        cls="s-apt", official=False),
    "sidestore":   dict(label="SideStore Community", short="SideStore",
                        url="https://community-apps.sidestore.io/sidecommunity.json",
                        cls="s-side", official=True),
    "altstore":    dict(label="AltStore Official", short="AltStore",
                        url="https://apps.altstore.io",
                        cls="s-alts", official=True),
    "oatmealdome": dict(label="OatmealDome", short="OatmealDome",
                        url="https://altstore.oatmealdome.me",
                        cls="s-oat", official=True),
    "utm":         dict(label="UTM", short="UTM",
                        url="https://alt.getutm.app",
                        cls="s-utm", official=True),
    "ish":         dict(label="iSH", short="iSH",
                        url="https://ish.app/altstore.json",
                        cls="s-ish", official=True),
    "flycast":     dict(label="Flycast", short="Flycast",
                        url="https://flyinghead.github.io/flycast-builds/altstore.json",
                        cls="s-fly", official=True),
}

CATS = ["Social", "Photo & video", "Music & video", "AI", "Health",
        "Learning", "Productivity", "Travel", "Finance", "Utilities",
        "Games", "Other"]

GENRE_SECTOR = {
    "Social Networking": "Social", "News": "Social",
    "Photo & Video": "Photo & video", "Entertainment": "Photo & video",
    "Graphics & Design": "Photo & video",
    "Music": "Music & video",
    "Health & Fitness": "Health", "Medical": "Health", "Sports": "Health",
    "Education": "Learning",
    "Productivity": "Productivity", "Business": "Productivity", "Book": "Productivity",
    "Finance": "Finance",
    "Travel": "Travel", "Navigation": "Travel",
    "Utilities": "Utilities", "Developer Tools": "Utilities",
    "Reference": "Utilities", "Weather": "Utilities",
    "Lifestyle": "Lifestyle", "Shopping": "Lifestyle", "Food & Drink": "Lifestyle",
    "Stickers": "Social",
    "Games": "Games",
}

# Keyword fallback for apps with no App Store genre (checked in order).
CATEGORY_KEYWORDS = [
    ("Utilities", (
        "patcher", "appstore", "vpn", "proxy", "dns", "adblock", "ad blocker", "jailbreak",
        "trollstore", "sidestore", "altstore", "scarlet", "feather", "esign",
        "gbox", "livecontainer", "file manager", "unzip", "calculator",
        "weather", "alarm clock", "battery", "cleaner", "keyboard",
        "widget", "shortcuts", "automation", "terminal", "ssh",
        "screen record", "qr scanner", "compass", "flashlight",
        "remote control", "bluetooth",
    )),
    ("Games", (
        "emulator", "fut", "game", "gaming", "rpg", "shooter", "puzzle", "arcade", "racing",
        "tower defense", "roguelike", "platformer", "minecraft", "roblox",
        "pokemon", "mario", "zelda", "fifa", "call of duty",
        "fortnite", "grand theft", "chess", "poker", "slots", "casino",
        "zombie", "ninja", "dragon", "simulator", "tycoon",
        "sniper", "drift", "kart", "soccer", "basketball",
        "dungeon", "survival", "war game", "battle royale",
    )),
    ("Social", (
        "whatsapp", "telegram", "instagram", "facebook", "messenger",
        "twitter", "tiktok", "snapchat", "discord", "reddit", "pinterest",
        "threads", "dating", "tinder", "bumble", "social network",
    )),
    ("Photo & video", (
        "youtube", "video recorder", "screen recorder", "camera", "photo", "video editor", "picsart", "lightroom", "vsco",
        "capcut", "netflix", "twitch", "vlc", "media player",
        "movie", "anime", "film", "photo editor",
    )),
    ("Music & video", (
        "spotify", "mp3", "soundcloud", "shazam", "deezer",
        "tidal", "piano", "guitar", "drum", "podcast", "radio", "music player",
    )),
    ("Health", (
        "sixpack", "fitness", "workout", "health", "medical", "diet", "calorie",
        "sleep", "pedometer", "running", "yoga", "gym",
    )),
    ("Learning", (
        "learn", "education", "school", "course", "duolingo", "dictionary",
        "translate", "study", "exam",
    )),
    ("Productivity", (
        "diagram", "productivity", "todo", "task manager", "calendar", "office",
        "document", "pdf", "scanner", "password manager", "email",
        "note",
    )),
    ("Travel", (
        "travel", "flight", "hotel", "navigation", "gps",
        "transit", "airline", "trip",
    )),
    ("Finance", (
        "bank", "finance", "crypto", "bitcoin", "wallet", "stock",
        "trading", "budget", "payment",
    )),
    ("Lifestyle", (
        "shopping", "food", "recipe", "cook", "restaurant",
        "fashion", "wallpaper", "sticker",
    )),
]
_CAT_RX = [(sec, [re.compile(r"\b" + re.escape(k) + r"\b") for k in kws])
          for sec, kws in CATEGORY_KEYWORDS]

SEO_PREFIX = re.compile(r"^[a-z0-9.-]+\.(com|net|org|io|dev|app)\s*\|\s*", re.I)
SEO_INTRO = re.compile(r"^what is .*? IPA\?\s*", re.I)
def clean_desc(d):
    """Strip scraper SEO junk ('domain.tld |', 'What is X IPA?') from a description."""
    d = (d or "").strip()
    d = SEO_PREFIX.sub("", d)
    d = SEO_INTRO.sub("", d)
    return d.strip()
def is_junk_desc(d):
    """True when a description is empty, stub-short, or mostly non-Latin script."""
    if not d or len(d) < 40:
        return True
    latin = sum(1 for c in d if "a" <= c.lower() <= "z")
    return latin < len(d) * 0.5

def cmp_ver(x, y):
    """Compare dotted version strings. Returns -1/0/1, or None if unparseable."""
    px = [int(p) for p in re.findall(r"\d+", str(x or ""))]
    py = [int(p) for p in re.findall(r"\d+", str(y or ""))]
    if not px or not py:
        return None
    n = max(len(px), len(py))
    px += [0] * (n - len(px)); py += [0] * (n - len(py))
    for a, b in zip(px, py):
        if a != b:
            return -1 if a < b else 1
    return 0
def version_suspicious(a):
    """Mirror of the UI's 'suspicious, walk away' verdict: the sideloaded build
    claims a newer version than the App Store has."""
    itv = (a.get("it") or {}).get("v")
    lv = a.get("lv")
    return bool(itv and lv and cmp_ver(lv, itv) == 1)

def keyword_sector(low):
    """Best-guess category from app name + description text. '' if no match."""
    compact = re.sub(r"\s+", "", low)
    for (sec, kws), (_, rxs) in zip(CATEGORY_KEYWORDS, _CAT_RX):
        for kw, rx in zip(kws, rxs):
            if rx.search(low):
                return sec
            # long distinctive keywords also match inside single tokens
            # (XPatcher, FlowdiaDiagrams) where word boundaries fail
            if len(kw) >= 6 and kw.replace(" ", "") in compact:
                return sec
    return ""

AI_KEYWORDS = ("chatgpt", "gpt-4", "gpt4", "gemini", "midjourney", "copilot",
               "assistant", "llm", "stable diffusion", "dall-e", "dalle",
               "ai chat", "ai art", "a.i.")

TWEAK_KEYWORDS = (
    "premium unlocked", "subscription unlocked", "pro unlocked", "vip unlocked",
    "unlocked", "premium injected", "injected", "tweaked", "modded", "no ads",
    "adblock", "ad block", "no jailbreak", "decrypted", "++", "plus",
    "no plugins", "sideload fix", "jit", "ipa",
)

PLACEHOLDER_BUNDLE = re.compile(r"^(com\.(unknown|example)|unknown)", re.I)
BUNDLE_OK = re.compile(r"^[A-Za-z0-9][\w.\-]*\.[A-Za-z0-9][\w.\-]*$")

MIN_RATINGS_TOP = 5000   # top-rated section quality gate
VH_CAP = 30              # version-history entries kept per source
VARIANTS_CAP = 40


# ---------------------------------------------------------------- fetch ----
def fetch_json(url, timeout=60, retries=3):
    """GET a URL and parse the JSON body, with exponential-backoff retries
    on transient failures. Raises the last exception when all retries fail.

    Prompt 19 follow-up (2026-10-30): urllib dies with IncompleteRead on
    large feeds through the egress proxy (alans' ~17MB repo.json fails
    deterministically this way — same failure class ipaomtk hit in
    2026-10-13). When urllib exhausts its retries, fall back to curl,
    which completes these downloads reliably."""
    req = urllib.request.Request(url, headers={"User-Agent": "APP_HORDE-build/1.0"})
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            last = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # 1s, 2s — ride out transient hiccups (2026-10-05: all 13 sources timed out twice in one morning)
    try:
        raw = _ipaomtk_get_curl(url, timeout=max(timeout, 120))
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception as e:
        print(f"  ! fetch_json curl fallback also failed for {url[:70]}: {e}",
              flush=True)
    raise last


IPAOMTK_API = "https://ipaomtk.com/wp-json/wp/v2/posts"
IPAOMTK_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) APP_HORDE-build/1.0"


def _ipaomtk_get_curl(url, timeout=30):
    """GET via curl subprocess; returns raw bytes. Raises on failure.

    curl fallback because Python-urllib fetches to ipaomtk.com intermittently
    die with IncompleteRead(0 bytes read) through the egress proxy while
    curl succeeds (2026-10-13)."""
    import subprocess
    out = subprocess.run(
        ["curl", "-sL", "-m", str(timeout), "-A", IPAOMTK_UA, url],
        capture_output=True, timeout=timeout + 10)
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError(f"curl failed rc={out.returncode} "
                           f"err={out.stderr[:120]!r}")
    return out.stdout


def _ipaomtk_get(url, timeout=30, retries=3):
    """GET a URL with retries; returns raw bytes. Raises on final failure."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": IPAOMTK_UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e
            # urllib is flaky against ipaomtk through the proxy — try curl
            # before the next backoff sleep so one bad attempt doesn't stall
            # the whole crawl.
            try:
                return _ipaomtk_get_curl(url, timeout)
            except Exception as ce:
                last = ce
            time.sleep(1 + attempt)
    raise last


def _ipaomtk_dl_info(slug):
    """Scrape one ipaomtk download page.

    Returns (ipa_url, meta) where meta holds the JSON-LD name/version/date.
    (None, {}) when the page has no direct IPA link (news posts, etc.)."""
    html = _ipaomtk_get(f"https://ipaomtk.com/{slug}/download/").decode("utf-8", "ignore")
    ipa = None
    for u in re.findall(r'(?:href|src)="([^"]+)"', html):
        if u.lower().split("?")[0].endswith(".ipa"):
            if "file.ipaomtk.com" in u:
                ipa = u
                break
            if ipa is None:
                ipa = u
    meta = {}
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    if m:
        try:
            meta = json.loads(m.group(1))
        except Exception:
            meta = {}
    return ipa, meta


def fetch_ipaomtk(snap_path):
    """Crawl ipaomtk.com (WordPress): post list via wp-json, then each post's
    /download/ page for the direct IPA URL + JSON-LD metadata.

    Incremental: the snapshot keeps each post's `modified` stamp, so weekly
    refreshes only re-fetch pages for new/changed posts. Returns an
    AltStore-shaped {"apps": [...]} dict, the same shape the other
    snapshots use, so the normal pipeline consumes it untouched."""
    old = {}
    if os.path.exists(snap_path):
        try:
            old = {a["slug"]: a for a in
                   json.load(open(snap_path, encoding="utf-8")).get("apps", [])
                   if a.get("slug")}
        except Exception:
            old = {}
    posts = []
    page = 1
    while True:
        data = json.loads(_ipaomtk_get(
            f"{IPAOMTK_API}?per_page=100&page={page}&_fields=slug,title,modified"))
        if not data:
            break
        posts.extend(data)
        if len(data) < 100:
            break
        page += 1
    print(f"  ipaomtk: {len(posts)} posts listed", flush=True)
    todo = [p for p in posts
            if p["slug"] not in old or old[p["slug"]].get("modified") != p.get("modified")]
    print(f"  ipaomtk: {len(todo)} new/changed download pages to fetch", flush=True)

    fresh = {}
    if todo:
        from concurrent.futures import ThreadPoolExecutor

        def work(p):
            try:
                ipa, meta = _ipaomtk_dl_info(p["slug"])
            except Exception:
                return None
            if not ipa:
                return None
            name = str(meta.get("name") or "").split("|")[0].strip()
            name = re.sub(r"\s+IPA\s*$", "", name, flags=re.I).strip() or p["slug"]
            return dict(
                slug=p["slug"], modified=p.get("modified"),
                name=name, bundleIdentifier="",
                developerName="IPAOMTK",
                version=str(meta.get("softwareVersion") or "").lstrip("vV").strip(),
                versionDate=(meta.get("dateModified") or "")[:10],
                downloadURL=ipa,
                iconURL=str(meta.get("thumbnailUrl") or ""),
                localizedDescription="",
            )

        def _save_checkpoint(collected):
            slugs = {p["slug"] for p in posts}
            apps = [a for s, a in old.items() if s in slugs and s not in collected]
            apps.extend(collected.values())
            tmp = snap_path + ".part"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"apps": apps}, fh)
            os.replace(tmp, snap_path)

        with ThreadPoolExecutor(max_workers=10) as ex:
            for i, rec in enumerate(ex.map(work, todo)):
                if rec:
                    fresh[rec["slug"]] = rec
                if (i + 1) % 500 == 0:
                    print(f"  ipaomtk: {i + 1}/{len(todo)} pages", flush=True)
                    _save_checkpoint(fresh)
            _save_checkpoint(fresh)

    slugs = {p["slug"] for p in posts}
    apps = [a for s, a in old.items() if s in slugs and s not in fresh]
    apps.extend(fresh.values())
    out = {"apps": apps}
    os.makedirs(os.path.dirname(snap_path), exist_ok=True)
    with open(snap_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    print(f"  ipaomtk: snapshot holds {len(apps)} apps", flush=True)
    return out


QUARANTINE_PATH = os.path.join(HERE, "quarantine.json")


def validate_feed(data):
    """Strict schema gate for AltStore/SideStore-style JSON feeds
    (Prompt 19). Returns (ok, reason). A feed passes when the top level
    is an object with an 'apps' list, at least half the entries are
    objects, and at least one entry carries a name. Disparate source
    schemas (extra/missing optional fields) are fine — this gates
    structure, not taste."""
    if not isinstance(data, dict):
        return False, "top level is not a JSON object"
    apps = data.get("apps")
    if not isinstance(apps, list):
        return False, "no 'apps' list in feed"
    if not apps:
        return False, "'apps' list is empty"
    dicts = [a for a in apps if isinstance(a, dict)]
    if len(dicts) * 2 < len(apps):
        return False, f"only {len(dicts)}/{len(apps)} entries are objects"
    if not any(str(a.get("name") or "").strip() for a in dicts):
        return False, "no entry has a name"
    return True, ""


def load_quarantine():
    """quarantine.json {source_key: {reason, ts, url}}. {} when absent."""
    try:
        with open(QUARANTINE_PATH, encoding="utf-8") as fh:
            q = json.load(fh)
        return q if isinstance(q, dict) else {}
    except Exception:
        return {}


def save_quarantine(q):
    try:
        with open(QUARANTINE_PATH, "w", encoding="utf-8") as fh:
            json.dump(q, fh, indent=1, sort_keys=True)
    except Exception as e:
        print(f"  ! quarantine write failed: {e}", flush=True)


def load_sources(offline):
    """Fetch every configured source (or reuse snapshots in --offline mode).

    Returns {key: (meta, [app dicts])}. Prompt 19 hardening: a live feed
    must pass validate_feed() BEFORE it is used or snapshotted — a feed
    that changes schema overnight is quarantined (quarantine.json) and
    the build falls back to the last-good snapshot instead of dropping
    the source's apps or ingesting garbage. A source with no valid
    snapshot is skipped with a warning so one dead repo can't kill the
    build.
    """
    fetched = {}
    quarantine = load_quarantine()
    q_dirty = False
    for key, meta in SOURCES.items():
        snap = os.path.join(SOURCES_DIR, key + ".json")
        data = None
        if key == "ipaomtk":
            # WordPress crawl, not a JSON feed (see fetch_ipaomtk); the
            # crawl assembles its own records, so the feed-level schema
            # gate doesn't apply — record checks happen in iter_builds.
            try:
                if offline and os.path.exists(snap):
                    with open(snap, encoding="utf-8") as fh:
                        data = json.load(fh)
                else:
                    data = fetch_ipaomtk(snap)
            except Exception as e:
                print(f"  ! {key}: fetch failed ({e}) — skipped", flush=True)
                continue
        elif offline:
            try:
                with open(snap, encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception as e:
                print(f"  ! {key}: snapshot unreadable ({e}) — skipped",
                      flush=True)
                continue
        else:
            try:
                raw = fetch_json(meta["url"])
            except Exception as e:
                print(f"  ! {key}: fetch failed ({e}) — skipped", flush=True)
                continue
            ok, reason = validate_feed(raw)
            if ok:
                data = raw
                os.makedirs(SOURCES_DIR, exist_ok=True)
                with open(snap, "w", encoding="utf-8") as fh:
                    json.dump(data, fh)
                if key in quarantine:
                    del quarantine[key]
                    q_dirty = True
                    print(f"  + {key}: feed healthy again — quarantine "
                          f"cleared", flush=True)
            else:
                quarantine[key] = {"reason": reason,
                                   "ts": date.today().isoformat(),
                                   "url": meta["url"]}
                q_dirty = True
                print(f"  ! {key}: QUARANTINED ({reason})", flush=True)
                try:
                    with open(snap, encoding="utf-8") as fh:
                        data = json.load(fh)
                    ok2, reason2 = validate_feed(data)
                    if not ok2:
                        print(f"  ! {key}: last-good snapshot also invalid "
                              f"({reason2}) — skipped", flush=True)
                        continue
                    print(f"  ! {key}: using last-good snapshot "
                          f"({len(data.get('apps') or [])} entries)",
                          flush=True)
                except Exception:
                    print(f"  ! {key}: no usable snapshot — skipped",
                          flush=True)
                    continue
        apps = data.get("apps") if isinstance(data, dict) else None
        if not isinstance(apps, list):
            print(f"  ! {key}: no parseable apps list — skipped", flush=True)
            continue
        fetched[key] = (meta, apps)
        print(f"  + {key}: {len(apps)} entries", flush=True)
    if q_dirty or not os.path.exists(QUARANTINE_PATH):
        save_quarantine(quarantine)
    return fetched


# ------------------------------------------------------------ normalize ----
def norm_date(s):
    """Normalize a version date to 'YYYY-MM-DD', or '' when unparseable."""
    if not s:
        return ""
    s = str(s).strip()[:10]
    return s if re.match(r"^\d{4}-\d{2}-\d{2}$", s) else ""


def parse_alans_versions(v):
    """Alan's 'versions' is a python-repr string; fall back to single version."""
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip().startswith("["):
        try:
            out = ast.literal_eval(v)
            return out if isinstance(out, list) else []
        except Exception:
            return []
    return []


def clean_text(*parts):
    """Join text parts, dropping empties and collapsing whitespace."""
    t = " ".join(p for p in parts if p).strip()
    t = re.sub(r"\s+", " ", t)
    return t


def clean_name(name):
    """Strip uploader version suffixes like 'GeniePro_7.1.2' or 'Foo (2.0.0)'."""
    cleaned = re.sub(r"[\s_\-–.()]*v?\d+(\.\d+){1,3}[\s_\-–.()]*$", "", name).strip()
    return cleaned or name


def parse_size(a, v):
    """Build size in bytes: version-level `size`, else app-level. 0 when missing."""
    for val in (v.get("size"), a.get("size")):
        if val is None:
            continue
        try:
            n = int(val)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return 0


# Prompt 19: download-URL discipline. Plain-HTTP and known
# shortener/monetizer hosts are never cataloged; redirect chains that
# don't land on a direct file are caught by the link-health probe (see
# exclude_redirect_chains). Drops are counted per source + reason in
# INGEST_STATS and printed by main().
SHORTENER_HOSTS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "rb.gy", "cutt.ly",
    "is.gd", "adf.ly", "ouo.io", "linkvertise.com", "shorturl.at",
    "rebrand.ly", "bl.ink", "s.id", "v.gd", "x.co", "fold.am",
}
INGEST_STATS = {"dropped": {}}


def url_block_reason(url):
    """Why a download URL must not be cataloged, or None when clean.
    An empty URL is not blocked here — records without a download URL
    are the assembler's business, as before."""
    if not url:
        return None
    low = url.lower()
    if low.startswith("http://"):
        return "plain-http"
    if not low.startswith("https://"):
        return "non-https-scheme"
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return "unparseable-url"
    host = host.split("@")[-1].split(":")[0]
    if host in SHORTENER_HOSTS:
        return "shortener-host"
    return None


def iter_builds(key, meta, apps):
    """Yield flat build records from one source's app list."""
    for a in apps:
        if not isinstance(a, dict):
            continue
        name = clean_name(str(a.get("name") or "").strip())
        if not name:
            continue
        bundle = str(a.get("bundleIdentifier") or a.get("bundleID") or "").strip()
        dev = str(a.get("developerName") or "").strip()
        icon = str(a.get("iconURL") or a.get("icon") or "").strip()
        base_text = clean_text(a.get("subtitle"), a.get("localizedDescription"))
        base = dict(name=name, bundle=bundle, dev=dev, icon=icon,
                    minos=str(a.get("minOSVersion") or "").strip())

        versions = []
        if key == "alans":
            versions = parse_alans_versions(a.get("versions"))
            if not versions:
                versions = [dict(version=a.get("version"), date=a.get("versionDate"),
                                 downloadURL=a.get("downloadURL"),
                                 localizedDescription=base_text)]
        elif isinstance(a.get("versions"), list) and a["versions"]:
            versions = a["versions"]
        else:
            versions = [dict(version=a.get("version"), date=a.get("versionDate") or a.get("fullDate"),
                             downloadURL=a.get("downloadURL"),
                             localizedDescription=base_text or a.get("versionDescription"))]

        for v in versions:
            if not isinstance(v, dict):
                continue
            url = str(v.get("downloadURL") or a.get("downloadURL") or "").strip()
            reason = url_block_reason(url)
            if reason:
                d = INGEST_STATS["dropped"].setdefault(key, {})
                d[reason] = d.get(reason, 0) + 1
                continue
            vbundle = str(v.get("bundleIdentifier") or "").strip() or bundle
            text = clean_text(v.get("localizedDescription"), v.get("versionDescription"))
            if not text:
                text = base_text
            rec = dict(base)
            rec.update(bundle=vbundle,
                       version=str(v.get("version") or a.get("version") or "").strip(),
                       date=norm_date(v.get("date") or v.get("versionDate") or a.get("versionDate") or a.get("fullDate")),
                       text=text[:500], url=url,
                       cl=clean_text(v.get("localizedDescription"),
                                     v.get("versionDescription"))[:300],
                       size=parse_size(a, v))
            yield rec


# ------------------------------------------------------------ seed cache ---
def split_db(html_text):
    """Extract and parse the `const DB = {...}` JSON blob from a built page.

    Brace-matches past quoted strings (which may contain braces). Raises
    (ValueError/IndexError) on malformed input — callers catch Exception.
    """
    marker = "const DB = "
    i = html_text.find(marker)
    if i < 0:
        return None
    i += len(marker)
    depth, instr, esc = 0, False, False
    j = i
    while True:
        ch = html_text[j]
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
        else:
            if ch == '"':
                instr = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
        j += 1
    return json.loads(html_text[i:j + 1])


def load_db_from_data(data_dir):
    """Reassemble the DB from data/meta.json + data/cats/*.json (split build).

    Inverse of render()'s split. Raises on missing/corrupt files.
    """
    with open(os.path.join(data_dir, "meta.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    cats = {}
    for sector, fname in meta.get("catfiles", {}).items():
        with open(os.path.join(data_dir, fname), encoding="utf-8") as fh:
            cats[sector] = json.load(fh)
    db = dict(meta)
    db["cats"] = cats
    del db["catfiles"]
    return db


def previous_db(path):
    """Return the previous build's DB for seeding: tries the inline
    `const DB = ...` first (old single-file builds), then data/meta.json
    next to `path` (split builds). {} when neither exists (first build).
    """
    if os.path.exists(path):
        try:
            db = split_db(open(path, encoding="utf-8").read())
            if db:
                return db
        except Exception as e:
            print(f"  ! inline seed parse failed: {e}", flush=True)
    data_dir = os.path.join(os.path.dirname(os.path.abspath(path)), "data")
    try:
        return load_db_from_data(data_dir)
    except Exception as e:
        print(f"  ! data-dir seed failed ({data_dir}): {e}", flush=True)
    return {}


def load_seed(path):
    """Seed maps from the previous build: name -> it/desc/tweak/sector/icon."""
    seed = {}
    db = previous_db(path)
    if not db:
        return seed, {}
    for apps in db.get("cats", {}).values():
        for a in apps:
            seed[norm_name(a["name"])] = {"it": a.get("it"), "desc": a.get("desc"),
                                          "tweak": a.get("tweak"), "sector": a.get("sector"),
                                          "icon": a.get("icon")}
    print(f"  seed: {len(seed)} app cache entries from previous build", flush=True)
    return seed, db


# ------------------------------------------------------------ vt cache ----
def newest_dl_url(a):
    """Newest build download URL for an app DB record, by build date.

    Shared with vt-scan.py — both must resolve the same URL per app or the
    cache lookup misses.
    """
    best = ("", "")
    for lst in (a.get("vh") or {}).values():
        if lst and len(lst[0]) > 2 and lst[0][2]:
            cand = (lst[0][1] or "", lst[0][2])
            if cand > best:
                best = cand
    if best[1]:
        return best[1]
    for u in (a.get("dl") or {}).values():
        if u:
            return u
    return ""


def load_vt_cache():
    """VirusTotal verdict cache {download_url: {...}}, or {} if absent."""
    p = os.path.join(HERE, "vt_cache.json")
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def vt_verdict(vt_cache, a):
    """Compact vt {m,s,t,d} for an app record, or None when unscored."""
    e = vt_cache.get(newest_dl_url(a)) or {}
    if e.get("status") != "scored":
        return None
    return {"m": int(e.get("malicious") or 0),
            "s": int(e.get("suspicious") or 0),
            "t": int(e.get("total") or 0),
            "d": str(e.get("scanned_date") or "")}


# ------------------------------------------------------------ itunes -------
def itunes_lookup(bundles):
    """Batch lookup bundle IDs via Apple's lookup API. Returns {bundle: info}."""
    out = {}
    bundles = sorted(set(bundles))
    for i in range(0, len(bundles), 50):
        batch = bundles[i:i + 50]
        url = ("https://itunes.apple.com/lookup?bundleId=" +
               ",".join(urllib.request.quote(b, safe="") for b in batch) +
               "&entity=software&limit=200")
        try:
            data = fetch_json(url, timeout=30)
            for r in data.get("results", []):
                b = r.get("bundleId")
                if not b:
                    continue
                art = r.get("artworkUrl100") or ""
                out[b] = {
                    "name": r.get("trackName") or "",
                    "r": round(float(r.get("averageUserRating") or 0), 5),
                    "c": int(r.get("userRatingCount") or 0),
                    "g": r.get("primaryGenreName") or "",
                    "v": r.get("version") or "",
                    "m": r.get("minimumOsVersion") or "",
                    "ss": (r.get("screenshotUrls") or [])[:3],
                    "art": art.replace("100x100bb.jpg", "256x256bb.jpg") if art else "",
                    "d": (r.get("description") or "").strip(),
                }
        except Exception as e:
            print(f"  ! itunes batch failed: {e}", flush=True)
        time.sleep(1.0)
    return out


# ------------------------------------------------------- tools watch -----
TOOLS_WATCH = [
    ("SideStore", "SideStore/SideStore"),
    ("LiveContainer", "LiveContainer/LiveContainer"),
    ("AltStore", "altstoreio/AltStore"),
]
TOOLS_TTL = 6 * 3600  # seconds between GitHub release re-checks


def load_tools_watch(offline=False):
    """Latest GitHub releases for the installer tools the guide recommends.

    Cache: tools_watch.json {repo: {name, version, date, url, ts}} with a
    6h TTL so repeat builds don't hammer the public API. Fails open: any
    fetch error keeps whatever the cache had (possibly nothing), and the
    build never dies on this. Returns [{name, version, date, url}] in
    TOOLS_WATCH order, entries with no known version omitted.
    """
    p = os.path.join(HERE, "tools_watch.json")
    cache = {}
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        if isinstance(d, dict):
            cache = d
    except Exception:
        cache = {}
    now = time.time()
    changed = False
    for name, repo in TOOLS_WATCH:
        ent = cache.get(repo)
        if ent and now - (ent.get("ts") or 0) < TOOLS_TTL:
            continue
        if offline:
            continue
        try:
            d = fetch_json(
                f"https://api.github.com/repos/{repo}/releases/latest",
                timeout=30, retries=2)
            cache[repo] = {"name": name,
                           "version": str(d.get("tag_name") or ""),
                           "date": str(d.get("published_at") or "")[:10],
                           "url": str(d.get("html_url") or ""),
                           "ts": now}
            changed = True
        except Exception as e:
            print(f"  ! tools watch {repo}: {e} — keeping cache", flush=True)
    if changed:
        try:
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, ensure_ascii=True, sort_keys=True)
        except Exception as e:
            print(f"  ! tools_watch write failed: {e}", flush=True)
    out = []
    for name, repo in TOOLS_WATCH:
        ent = cache.get(repo)
        if ent and ent.get("version"):
            out.append({"name": name, "version": ent["version"],
                        "date": ent.get("date", ""),
                        "url": ent.get("url", "")})
    return out


# ------------------------------------------------------ screenshots ------
def load_screenshots_cache():
    """Screenshot URL cache {bundle_id: [urls]} for WP6, built
    incrementally by build_db. {} when absent/corrupt."""
    p = os.path.join(HERE, "screenshots.json")
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_screenshots_cache(cache):
    """Persist the screenshots cache (sorted keys, stable bytes)."""
    try:
        with open(os.path.join(HERE, "screenshots.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=True, sort_keys=True)
    except Exception as e:
        print(f"  ! screenshots write failed: {e}", flush=True)


# ------------------------------------------------------------ assemble ----
def valid_bundle(b):
    """True for a plausible real bundle id (dotted, 2+ labels, not a
    uploader placeholder like com.unknown / com.example / unknown)."""
    return bool(b) and bool(BUNDLE_OK.match(b)) and not PLACEHOLDER_BUNDLE.match(b)


def norm_name(n):
    """Canonical app identity: lowercase alphanumerics only. Apps are
    grouped by this, not by bundle id, because tweaked builds reuse
    junk/placeholder bundle ids across unrelated apps."""
    return re.sub(r"[^a-z0-9]", "", n.lower())


def extract_tweak(texts):
    """Comma-joined tweak labels (e.g. 'pro unlocked, no ads') found in the
    build descriptions, plus any 'Variant: <Name>' uploader tags."""
    found = []
    blob = " | ".join(texts).lower()
    for kw in TWEAK_KEYWORDS:
        if kw in blob and kw not in found:
            found.append(kw)
    # "Variant: <Name>" / "<Name> v1.2" patterns
    for t in texts:
        m = re.search(r"[Vv]ariant:\s*([A-Za-z0-9+_. ]{2,28})", t)
        if m:
            v = "variant: " + m.group(1).strip()
            if v not in found:
                found.append(v)
    return ", ".join(found[:8])


def bundle_rank(recs):
    """Distinct bundle ids across an app's build records, most common
    valid ones first; junk/placeholder ids trail so App Store lookups
    try the real identities first."""
    cnt = Counter(r["bundle"] for r in recs if r["bundle"])
    valid = sorted([b for b in cnt if valid_bundle(b)], key=lambda b: -cnt[b])
    junk = sorted([b for b in cnt if not valid_bundle(b)], key=lambda b: -cnt[b])
    return valid + junk


def has_it(it):
    """True when an App Store lookup result is a real match (has a name)."""
    return bool(it and it.get("name"))


def build_db(fetched, seed, old_db, tools=None):
    """Assemble the site DB from fetched sources.

    fetched: {source_key: (meta, [app dicts])} from load_sources().
    seed:    name-keyed {it, desc, tweak, sector, icon} carried over from the
             previous index.html so only NEW bundle ids hit Apple's API.
    old_db:  the previous DB (only used to keep the Monday-refresh schedule
             block stable across rebuilds).
    tools:   [{name, version, date, url}] from load_tools_watch(), baked
             in as db["tools"] for the guide's LATEST TOOL VERSIONS block.

    Returns (db, verified_count). `db` is JSON-serializable; render()
    splits it into data/meta.json + data/cats/*.json next to index.html.
    """
    # Group by normalized app NAME (tweaked builds reuse junk/placeholder bundle
    # ids like com.karbinstoree across unrelated apps; the name is the stable
    # identity). All distinct bundle ids are collected into the bundle list.
    groups = defaultdict(list)
    for key, (meta, apps) in fetched.items():
        for rec in iter_builds(key, meta, apps):
            rec["src"] = key
            groups[norm_name(rec["name"])].append(rec)

    print(f"  grouped into {len(groups)} apps", flush=True)

    vt_cache = load_vt_cache()
    print(f"  vt cache: {len(vt_cache):,} urls", flush=True)

    shots_cache = load_screenshots_cache()
    pending_shots = []  # (app record, bundle id) awaiting a capped lookup

    # which bundles need fresh App Store lookups? (seed is name-keyed)
    need = set()
    for gkey, recs in groups.items():
        sit = seed.get(gkey, {}).get("it")
        if not has_it(sit):
            for b in bundle_rank(recs):
                need.add(b)
        elif not (sit or {}).get("d"):
            # seeded App Store record predates description capture — backfill it
            for b in bundle_rank(recs)[:2]:
                need.add(b)
        elif "m" not in (sit or {}) or "ss" not in (sit or {}):
            # seeded App Store record predates minimum-iOS / screenshot
            # capture — backfill it (key-presence check: empty lists are
            # valid results and must not trigger endless re-backfills)
            for b in bundle_rank(recs)[:2]:
                need.add(b)
    print(f"  itunes lookups needed: {len(need)} (rest seeded)", flush=True)
    fresh_it = itunes_lookup(need) if need else {}

    def app_it(gkey, recs):
        s = seed.get(gkey, {}).get("it")
        if has_it(s) and "m" in (s or {}) and "ss" in (s or {}):
            return s
        for b in bundle_rank(recs):
            it = fresh_it.get(b)
            if has_it(it):
                return it
        return s if has_it(s) else None

    today = date.today()
    apps = []
    for gkey, recs in groups.items():
        recs.sort(key=lambda r: (r["date"] or "", r["version"] or ""), reverse=True)
        names = Counter(r["name"] for r in recs)
        name = names.most_common(1)[0][0]
        devs = sorted({r["dev"] for r in recs if r["dev"]})[:12]
        dev = Counter(r["dev"] for r in recs if r["dev"]).most_common(1)
        dev = dev[0][0] if dev else ""
        bundles = bundle_rank(recs)
        seedrec = seed.get(gkey, {})

        per = dict(Counter(r["src"] for r in recs))
        srcs = sorted(per, key=lambda s: list(SOURCES).index(s))
        n = len(recs)

        texts = [r["text"] for r in recs if r["text"]]
        variants, seen = [], set()
        for t in texts:
            t80 = t[:80]
            if t80 not in seen:
                seen.add(t80)
                variants.append(t80)
            if len(variants) >= VARIANTS_CAP:
                break

        tweak = seedrec.get("tweak") or extract_tweak(texts)

        it = app_it(gkey, recs)
        if it and not it.get("d"):
            for b in bundle_rank(recs):
                f = fresh_it.get(b)
                if f and f.get("d"):
                    it["d"] = f["d"]
                    break

        # WP6 screenshots: screenshots.json cache first, then the App
        # Store record's own ss list; verified apps with neither queue
        # their best bundle for the capped post-loop lookup.
        shots = []
        if it:
            for b in bundles:
                c = shots_cache.get(b)
                if c:
                    shots = list(c)[:3]
                    break
            if not shots and it.get("ss"):
                shots = list(it["ss"])[:3]

        genre = (it or {}).get("g", "")
        low = (name + " " + " ".join(texts[:3])).lower()
        if any(k in low for k in AI_KEYWORDS):
            sector = "AI"
        else:
            sector = GENRE_SECTOR.get((genre or "").strip()) or keyword_sector(low) or "Other"
        if sector == "Other":
            # never downgrade a decent historical assignment into the sink
            old = seedrec.get("sector")
            if old and old != "Other":
                sector = old

        icon = seedrec.get("icon") or next((r["icon"] for r in recs if r["icon"]), "")
        fb = (it or {}).get("art", "")
        if not icon:
            icon = fb

        latest = recs[0]
        upd_date = latest["date"]
        try:
            upd = (today - datetime.strptime(upd_date, "%Y-%m-%d").date()).days
            upd = max(0, upd)
        except Exception:
            upd, upd_date = None, upd_date or ""

        vh = {}
        for r in recs:
            lst = vh.setdefault(r["src"], [])
            if len(lst) < VH_CAP and r["url"]:
                entry = [r["version"], r["date"], r["url"]]
                if r.get("cl"):
                    entry.append(r["cl"])  # optional 4th element: changelog
                lst.append(entry)
        dl = {s: v[0][2] for s, v in vh.items() if v}

        minos = ""
        for r in recs:
            if r["minos"] and r["minos"] > minos:
                minos = r["minos"]

        risk = []
        for rb in bundles:
            if not valid_bundle(rb):
                risk.append(["warn", f"a build ships under a placeholder bundle id "
                                     f"({rb[:18]}…) — identity unverified"])
                break

        desc = clean_desc(seedrec.get("desc"))
        if is_junk_desc(desc):
            desc = clean_desc((it or {}).get("d", ""))
            if len(desc) > 320:
                desc = desc[:320].rsplit(" ", 1)[0] + "…"
        if is_junk_desc(desc):
            bits = []
            if genre:
                bits.append(genre + " app")
            if tweak:
                bits.append("builds: " + tweak[:90])
            else:
                bits.append("community build")
            desc = " — ".join(bits) + f" · via {', '.join(SOURCES[s]['short'] for s in srcs)}"

        rec = {
            "name": name, "desc": desc, "tweak": tweak,
            "bundle": bundles,
            "n": n, "nA": per.get("alans", 0), "nT": per.get("apptesters", 0),
            "per": per, "dev": dev, "devs": devs, "sector": sector,
            "icon": icon, "fb": fb, "it": it, "src": srcs,
            "upd": upd, "updDate": upd_date, "lv": latest["version"],
            "variants": variants, "dl": dl, "vh": vh,
            "minos": minos, "risk": risk,
        }
        sz = max((r.get("size") or 0 for r in recs), default=0)
        if sz:
            rec["sz"] = sz
        vt = vt_verdict(vt_cache, rec)
        if vt:
            rec["vt"] = vt
        if shots:
            rec["shots"] = shots
        elif it and not any(b in shots_cache for b in bundles):
            cand = next((b for b in bundles if valid_bundle(b)), "")
            if cand:
                pending_shots.append((rec, cand))
        apps.append(rec)

    # WP6: capped incremental screenshot lookups (300/build), fail open.
    if pending_shots:
        want = pending_shots[:300]
        print(f"  screenshots: {len(want)} new lookups (cap 300)", flush=True)
        try:
            got = itunes_lookup([b for _, b in want])
        except Exception as e:
            print(f"  ! screenshots lookup failed: {e}", flush=True)
            got = {}
        for rec, b in want:
            ss = list((got.get(b) or {}).get("ss") or [])[:3]
            shots_cache[b] = ss
            if ss:
                rec["shots"] = ss
        save_screenshots_cache(shots_cache)

    # editors' choice rail: resolve curated.json names against the index
    curated_entries = []
    try:
        with open(os.path.join(HERE, "curated.json"), encoding="utf-8") as fh:
            curated_entries = json.load(fh)
    except Exception as e:
        print(f"  ! curated.json unreadable: {e}", flush=True)
    app_by_norm = {norm_name(a["name"]): a for a in apps}
    curated = []
    for e in curated_entries:
        if not isinstance(e, dict):
            continue
        a = app_by_norm.get(norm_name(str(e.get("name") or "")))
        if a:
            curated.append({"name": a["name"],
                            "blurb": str(e.get("blurb") or "")[:160],
                            "icon": a.get("icon") or "",
                            "n": a["n"]})
    print(f"  curated: {len(curated)}/{len(curated_entries)} matched", flush=True)

    apps.sort(key=lambda a: (-a["n"], a["name"].lower()))
    cats = {c: [] for c in CATS}
    for a in apps:
        cats[a["sector"] if a["sector"] in cats else "Other"].append(a)

    total = sum(a["n"] for a in apps)
    verified = sum(1 for a in apps if a["it"])
    toprated = sorted(
        [a for a in apps if a["it"] and a["it"]["c"] >= MIN_RATINGS_TOP
         and not version_suspicious(a)],
        key=lambda a: (-a["it"]["r"], -a["it"]["c"]))[:12]

    repos = {k: {"entries": len(ma)} for k, (_, ma) in fetched.items()}
    db = {
        "cats": cats,
        "counts": {c: len(v) for c, v in cats.items()},
        "top": [a for a in sorted(apps, key=lambda a: -a["n"])
                if not version_suspicious(a)][:12],
        "toprated": toprated,
        "total": total,
        "unique": len(apps),
        "nsources": len(fetched),
        "repos": repos,
        "curated": curated,
        "tools": tools or [],
        "srcmeta": {k: {"label": SOURCES[k]["short"].upper(),
                        "cls": SOURCES[k]["cls"],
                        "official": SOURCES[k]["official"]}
                    for k in fetched},
        "refresh": (old_db.get("refresh") if old_db else None) or
                   {"dow": 1, "hh": 6, "mm": 37, "tz": "America/Chicago"},
    }
    return db, verified


# ------------------------------------------------------------ render -----
SITE_URL = "https://crtghoul.github.io/app-horde"


def write_rss(db):
    """Static RSS 2.0 feed of the fresh-drops list (builds ≤7 days old)."""
    from email.utils import formatdate
    fresh = [a for c in db["cats"].values() for a in c
             if a.get("upd") is not None and a["upd"] <= 7
             and not version_suspicious(a)]
    fresh.sort(key=lambda a: (a["upd"], -a["n"], a["name"].lower()))
    items = []
    for a in fresh[:50]:
        try:
            pub = formatdate(
                datetime.strptime(a["updDate"], "%Y-%m-%d").timestamp(),
                usegmt=True)
        except Exception:
            pub = formatdate(usegmt=True)
        link = f"{SITE_URL}/#app={urllib.parse.quote(a['name'])}"
        desc = (f"v{a.get('lv', '')} — {a.get('desc', '')[:280]} "
                f"(via {', '.join(a.get('src', []))})")
        items.append(
            "  <item>\n"
            f"    <title>{esc_h(a['name'])} v{esc_h(a.get('lv', ''))}</title>\n"
            f"    <link>{link}</link>\n"
            f"    <guid>{link}</guid>\n"
            f"    <pubDate>{pub}</pubDate>\n"
            f"    <description>{esc_h(desc)}</description>\n"
            "  </item>")
    rss = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0">\n<channel>\n'
        f"  <title>app-horde — fresh drops</title>\n"
        f"  <link>{SITE_URL}/</link>\n"
        "  <description>Apps with new builds indexed by app-horde in the last "
        "7 days. Refreshed every monday.</description>\n"
        f"  <lastBuildDate>{formatdate(usegmt=True)}</lastBuildDate>\n"
        + "\n".join(items)
        + "\n</channel>\n</rss>\n")
    out = os.path.join(HERE, "fresh.xml")
    with open(out, "w", encoding="utf-8") as f:
        f.write(rss)
    print(f"  wrote {out} ({len(items)} items)")


def esc_h(s):
    """HTML-escape a string for safe embedding in the rendered page."""
    return html.escape(str(s), quote=True)


def render(db, verified, out_path):
    """Render index.html: template_head.html with {{PLACEHOLDERS}} filled,
    then template_tail.html. The DB is NOT inlined — it is split into
    data/meta.json + data/cats/<sector>.json next to the output file, and
    the page fetches them at load (see loadDB() in template_head.html).

    The split keeps every output file under Cloudflare Pages' 25 MiB
    per-file limit (asserted below with 20 MiB headroom).

    Every placeholder must exist in the head template (asserted) so a
    template edit that drops one fails loudly instead of shipping a page
    with raw {{TAGS}} in it. Returns the short SHA-256 digest of the
    dataset, which the weekly action prints so no-op rebuilds can skip
    the commit.
    """
    head = open(os.path.join(HERE, "template_head.html"), encoding="utf-8").read()
    tail = open(os.path.join(HERE, "template_tail.html"), encoding="utf-8").read()

    unique, total, nsrc = db["unique"], db["total"], db["nsources"]
    pct = round(100 * verified / unique) if unique else 0
    labels = [SOURCES[k]["label"] for k in db["repos"]]
    shorts = [SOURCES[k]["short"] for k in db["repos"]]
    data_date = max((a["updDate"] for c in db["cats"].values()
                     for a in c if a["updDate"]), default=str(date.today()))

    tiles = "".join(
        f'<div class="tile" data-t="{c}"><b>{len(db["cats"][c]):,}</b>'
        f'<span>{c.lower()}</span></div>' for c in CATS)

    # sources pasteable straight into AltStore/SideStore (ipaomtk is a
    # WordPress site, not an AltStore feed — indexed, but not pasteable)
    direct = [k for k in db["repos"] if SOURCES[k].get("direct", True)]
    src_rows = "".join(
        f'  <div class="bid"><code>{esc_h(SOURCES[k]["url"])}</code>'
        f'<button class="cpy" data-c="{esc_h(SOURCES[k]["url"])}">COPY</button></div>\n'
        for k in direct)
    dim_bits = []
    for k in direct:
        star = "★ " if SOURCES[k]["official"] else ""
        dim_bits.append(f"{star}{SOURCES[k]['label']} ({db['repos'][k]['entries']:,} builds)")
    dim_line = ("  <p class=\"dim\">★ = official developer source — straight from the app "
                "maker, not a tweak repo.<br>" + " · ".join(dim_bits) + ".</p>")

    src_options = "".join(
        f'      <option value="{k}">{esc_h(SOURCES[k]["short"])}</option>\n'
        for k in db["repos"])

    sub = {
        "{{META_DESC}}": f"Browse {unique:,} sideloadable iOS apps from {nsrc} repos "
                         f"({', '.join(shorts)}) — App Store-verified, with risk signals, "
                         f"version history, and a beginner field manual. Free, no signup.",
        "{{OG_DESC}}": f"{unique:,} apps · {total:,} builds indexed, App Store-verified, "
                       f"with risk signals and a beginner field manual. Free, no signup.",
        "{{TW_DESC}}": f"{unique:,} sideloadable iOS apps, App Store-verified, with risk signals. Free, no signup.",
        "{{HERO_LINE}}": f"{unique:,} apps indexed · {total:,} builds analyzed · "
                         f"{pct}% verified against the App Store. Select a category, run a query, "
                         f"sort by build count — or skim the top rated.",
        "{{STAT_HOSTS}}": f"{unique:,}",
        "{{STAT_STRAINS}}": f"{total:,}",
        "{{STAT_SOURCES}}": str(nsrc),
        "{{TILES}}": f'    <div class="tiles">{tiles}</div>',
        "{{GUIDE_SRC_INTRO}}": (f"AltStore and SideStore can browse these {len(direct)} repos directly. "
                                f"Go to the <b>Sources</b> tab, tap <b>+</b>, and paste these:"),
        "{{GUIDE_SRC_ROWS}}": src_rows + dim_line,
        "{{GUIDE_DISCLAIMER_SRC}}": (f"Every ⬇ GET button hands you a file from one of the "
                                     f"{nsrc} indexed repos listed below"),
        "{{SRC_OPTIONS}}": src_options.rstrip("\n"),
        "{{REPO_TILES}}": (
            '    <div class="tiles">\n' + "".join(
                f'    <div class="tile rtile" data-repo="{k}"><b>'
                f"{sum(1 for sec in db['cats'].values() for a in sec if k in a['src']):,}</b>"
                f'<span>{esc_h(SOURCES[k]["short"].lower())}</span></div>\n'
                for k in db["repos"]
            ) + '    </div>'
        ),

        "{{FOOTER_SOURCES}}": esc_h(" + ".join(l.lower() for l in labels)),
        "{{DATA_DATE}}": data_date,
    }
    for k, v in sub.items():
        assert k in head, f"placeholder {k} missing from template"
        head = head.replace(k, v)

    # Split the DB: data/meta.json (everything but cats) + one
    # data/cats/<sector>.json per sector. Deterministic: CATS order,
    # insertion-ordered JSON, same as the old inline blob.
    out_dir = os.path.dirname(os.path.abspath(out_path))
    data_dir = os.path.join(out_dir, "data")
    cats_dir = os.path.join(data_dir, "cats")
    os.makedirs(cats_dir, exist_ok=True)

    catfiles = {}
    digest_src = []
    for sector in CATS:
        apps = db["cats"].get(sector, [])
        slug = re.sub(r"[^a-z0-9]+", "-", sector.lower()).strip("-")
        fname = f"cats/{slug}.json"
        catfiles[sector] = fname
        blob = json.dumps(apps, ensure_ascii=True)
        fpath = os.path.join(data_dir, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(blob)
        sz = os.path.getsize(fpath)
        assert sz < 20 * 1024 * 1024, \
            f"{fname} is {sz:,} bytes — over the 20 MiB Pages headroom"
        digest_src.append(blob)
    meta = {k: v for k, v in db.items() if k != "cats"}
    meta["catfiles"] = catfiles
    meta_json = json.dumps(meta, ensure_ascii=True)
    with open(os.path.join(data_dir, "meta.json"), "w", encoding="utf-8") as f:
        f.write(meta_json)
    digest = hashlib.sha256(
        (meta_json + "".join(digest_src)).encode("utf-8")).hexdigest()[:16]

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(head)
        f.write(tail)
    print(f"  wrote {out_path} ({os.path.getsize(out_path):,} bytes) + "
          f"data/ ({len(catfiles)} cats files), db hash {digest}")
    return digest


# ---------------------------------------------------------------- main -----
def load_link_health_cache(cache_path=None):
    """Read link_health.json ({url: {"ok": bool, "ts": date}}). {} when
    absent/corrupt — the probe simply re-checks everything."""
    cache_path = cache_path or os.path.join(HERE, "link_health.json")
    try:
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)
        return cache if isinstance(cache, dict) else {}
    except Exception:
        return {}


def bake_dead_links(db, cache):
    """Bake known-dead download URLs into app records as rec["dead"]
    (sorted URL list, only URLs actually present in the app's vh), from
    the link-health cache. The UI badges dead builds in the version
    picker/modal and flags cards whose every build is dead. Returns the
    number of (app, url) dead flags baked."""
    dead_urls = {u for u, e in cache.items()
                 if isinstance(e, dict) and e.get("ok") is False}
    if not dead_urls:
        return 0
    n = 0
    for apps in db["cats"].values():
        for a in apps:
            mine = set()
            for lst in (a.get("vh") or {}).values():
                for entry in lst:
                    if len(entry) > 2 and entry[2] in dead_urls:
                        mine.add(entry[2])
            if mine:
                a["dead"] = sorted(mine)
                n += len(mine)
    return n


def exclude_redirect_chains(db, cache):
    """Remove builds whose download URL is a known redirect chain that
    doesn't land on a direct .ipa/.tipa (Prompt 19: never cataloged —
    excluded outright, unlike dead links which are badged). Apps left
    with no builds drop out of the DB. Returns (builds_removed,
    apps_removed)."""
    chained = {u for u, e in cache.items()
               if isinstance(e, dict) and e.get("reason") == "redirect-chain"}
    if not chained:
        return 0, 0
    nb = na = 0
    for sector in list(db["cats"]):
        kept = []
        for a in db["cats"][sector]:
            vh = a.get("vh") or {}
            gone = 0
            for src in list(vh):
                before = len(vh[src])
                vh[src] = [e for e in vh[src]
                           if not (len(e) > 2 and e[2] in chained)]
                gone += before - len(vh[src])
                if not vh[src]:
                    del vh[src]
            if gone:
                nb += gone
                a["n"] = sum(len(l) for l in vh.values())
                a["src"] = [s for s in (a.get("src") or []) if s in vh]
                if a.get("per"):
                    a["per"] = {s: len(vh[s]) for s in vh if s in a["per"]}
            if vh:
                kept.append(a)
            else:
                na += 1
        db["cats"][sector] = kept
        if isinstance(db.get("counts"), dict):
            db["counts"][sector] = len(kept)
    if nb:
        db["total"] = sum(a["n"] for apps in db["cats"].values()
                          for a in apps)
        db["unique"] = sum(len(apps) for apps in db["cats"].values())
    return nb, na


# Prompt 21: per-source version diffs -> data/changes.json, consumed by
# the site's [changes] view. IPFS mirroring of the dataset stays an
# experiment behind this flag — do NOT surface it in the UI until a
# real pin survives a week (Cesar's rule).
IPFS_MIRROR_ENABLED = False

CHANGES_CAP = 300


def compute_changes(db, old_db):
    """Diff this build against the previous one, per app + source.

    Returns [{name, sector, source, added: [{v, d}], removed: [v],
    new: bool}] newest first, capped at CHANGES_CAP. Brand-new apps
    appear once per source with their newest build as the addition."""
    old_idx = {}
    for apps in (old_db.get("cats") or {}).values():
        for a in apps:
            if a.get("name") and a["name"] not in old_idx:
                old_idx[a["name"]] = a
    entries = []
    for sector, apps in db["cats"].items():
        for a in apps:
            old = old_idx.get(a["name"])
            vh = a.get("vh") or {}
            if old is None:
                for src, lst in vh.items():
                    if lst:
                        entries.append(
                            {"name": a["name"], "sector": sector,
                             "source": src, "new": True,
                             "added": [{"v": lst[0][0], "d": lst[0][1]}],
                             "removed": []})
                continue
            ovh = old.get("vh") or {}
            for src, lst in vh.items():
                oldvers = {e[0] for e in ovh.get(src, [])}
                newvers = {e[0] for e in lst}
                added = [{"v": e[0], "d": e[1]} for e in lst
                         if e[0] not in oldvers]
                removed = sorted(oldvers - newvers)
                if added or removed:
                    entries.append(
                        {"name": a["name"], "sector": sector,
                         "source": src, "added": added, "removed": removed})

    def newest(e):
        return max((x.get("d") or "") for x in e["added"]) if e["added"] else ""

    entries.sort(key=newest, reverse=True)
    return entries[:CHANGES_CAP]


def write_changes(db, old_db, data_dir=None):
    """Write data/changes.json (Prompt 21). Returns the entry count."""
    data_dir = data_dir or os.path.join(HERE, "data")
    payload = {"generated": date.today().isoformat(),
               "changes": compute_changes(db, old_db)}
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "changes.json"), "w",
              encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    return len(payload["changes"])


def redirect_is_file(final_url, content_type, content_disp):
    """Does a redirect chain land on a direct file? Signals: the final
    URL names an .ipa/.tipa, the response is served as an attachment,
    or its content type is a binary/file type. A chain landing on an
    HTML page (or with no file signal at all) is NOT a direct file —
    e.g. github.com -> release-assets.githubusercontent.com serves the
    asset as application/octet-stream with a UUID path (no filename),
    which is a legitimate file download, not a landing page."""
    if re.search(r"\.(ipa|tipa)(\?|#|$)", final_url or "", re.I):
        return True
    if "attachment" in (content_disp or "").lower():
        return True
    ct = (content_type or "").lower()
    if ct and not ct.startswith("text/") and "html" not in ct:
        return True
    return False


def check_link_health(db, cap=2500, cache_path=None):
    """HEAD-check the newest build URL per (app, source); cache results.

    Cache: link_health.json {url: {"ok": bool, "ts": "YYYY-MM-DD"}}.
    URLs checked within 30 days are skipped. At most `cap` fresh probes
    per run (ThreadPoolExecutor, 8s timeout each; HEAD first, then a
    1-byte Range GET for servers that reject HEAD). Never raises — the
    caller treats it as best-effort.
    """
    from concurrent.futures import ThreadPoolExecutor
    cache_path = cache_path or os.path.join(HERE, "link_health.json")
    cache = load_link_health_cache(cache_path)
    today = date.today()

    def fresh(ts):
        try:
            return (today - datetime.strptime(ts, "%Y-%m-%d").date()).days < 30
        except Exception:
            return False

    urls = set()
    for apps in db["cats"].values():
        for a in apps:
            for lst in (a.get("vh") or {}).values():
                if lst and len(lst[0]) > 2 and lst[0][2]:
                    urls.add(lst[0][2])
    todo = [u for u in sorted(urls)
            if not (isinstance(cache.get(u), dict) and fresh(cache[u].get("ts", "")))]
    todo = todo[:cap]
    print(f"  link health: {len(urls):,} urls cached-checked, "
          f"{len(todo)} to probe (cap {cap})", flush=True)
    if not todo:
        return cache

    def probe(u):
        for method in ("HEAD", "GET"):
            try:
                headers = {"User-Agent": "APP_HORDE-build/1.0"}
                if method == "GET":
                    headers["Range"] = "bytes=0-0"
                req = urllib.request.Request(u, method=method, headers=headers)
                with urllib.request.urlopen(req, timeout=8) as r:
                    code = r.getcode()
                    final = r.geturl()
                    if final == u:
                        return (u, 200 <= code < 400, "")
                    is_file = redirect_is_file(
                        final, r.headers.get_content_type(),
                        r.headers.get("Content-Disposition"))
                    return (u, 200 <= code < 400, final if is_file else "CHAIN")
            except Exception:
                continue
        return (u, False, "")

    okc = badc = 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        for u, ok, final in ex.map(probe, todo):
            entry = {"ok": ok, "ts": today.isoformat()}
            if final == "CHAIN":
                # Prompt 19: redirect chain that does NOT land on a
                # direct file (see redirect_is_file) — never cataloged;
                # excluded at bake time (exclude_redirect_chains).
                entry["ok"] = False
                entry["reason"] = "redirect-chain"
            elif final:
                entry["redir"] = final
            cache[u] = entry
            okc += entry["ok"]
            badc += (not entry["ok"])
    try:
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
    except Exception as e:
        print(f"  ! link_health write failed: {e}", flush=True)
    print(f"  link health: {okc} ok, {badc} dead", flush=True)
    return cache


def main():
    """CLI: build.py [--offline] [--out <path>] [--skip-link-check].

    --offline reuses sources/*.json snapshots instead of downloading.
    --out    output path (default: index.html next to this script).
    --skip-link-check  bypass the post-render dead-link health probe.
    """
    offline = "--offline" in sys.argv
    skip_link = "--skip-link-check" in sys.argv
    out = "index.html"
    if "--out" in sys.argv:
        i = sys.argv.index("--out")
        if i + 1 >= len(sys.argv):
            sys.exit("error: --out needs a value")
        out = sys.argv[i + 1]
    out = os.path.join(HERE, out) if not os.path.isabs(out) else out

    print("fetching sources…", flush=True)
    fetched = load_sources(offline)
    if not fetched:
        sys.exit("no sources fetched — aborting")
    print("seeding from previous build…", flush=True)
    seed, old_db = load_seed(os.path.join(HERE, "index.html"))
    tools = load_tools_watch(offline)
    print(f"  tools watch: {len(tools)} tool releases known", flush=True)
    print("assembling DB…", flush=True)
    db, verified = build_db(fetched, seed, old_db, tools=tools)
    print(f"  unique={db['unique']:,} total builds={db['total']:,} "
          f"verified={verified:,}", flush=True)
    for key, reasons in sorted(INGEST_STATS["dropped"].items()):
        print(f"  url discipline [{key}]: dropped "
              f"{sum(reasons.values())} builds {reasons}", flush=True)
    # Dead-link health BEFORE render so the results bake into this build:
    # probe fresh URLs (cap 2500) on live builds; offline/skipped runs
    # bake whatever the cache already knows. Best-effort — never fatal.
    lh_cache = load_link_health_cache()
    if not offline and not skip_link:
        try:
            lh_cache = check_link_health(db)
        except Exception as e:
            print(f"  ! link health check failed (non-fatal): {e}", flush=True)
    elif offline:
        print("  link check skipped (offline) — baking cached results",
              flush=True)
    else:
        print("  link check skipped (--skip-link-check) — baking cached "
              "results", flush=True)
    ndead = bake_dead_links(db, lh_cache)
    print(f"  dead links baked: {ndead} urls", flush=True)
    nchain, nchainapps = exclude_redirect_chains(db, lh_cache)
    if nchain:
        print(f"  redirect-chain builds excluded: {nchain} "
              f"(apps dropped: {nchainapps})", flush=True)
    digest = render(db, verified, out)
    nch = write_changes(db, old_db)
    print(f"  changes.json: {nch} per-source diffs vs previous build",
          flush=True)
    write_rss(db)
    print(f"DONE db_hash={digest}")


if __name__ == "__main__":
    main()
