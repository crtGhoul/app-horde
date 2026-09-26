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
    "Games": "Games",
}

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
def fetch_json(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "APP_HORDE-build/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def load_sources(offline):
    fetched = {}
    for key, meta in SOURCES.items():
        snap = os.path.join(SOURCES_DIR, key + ".json")
        try:
            if offline and os.path.exists(snap):
                data = json.load(open(snap, encoding="utf-8"))
            else:
                data = fetch_json(meta["url"])
                os.makedirs(SOURCES_DIR, exist_ok=True)
                json.dump(data, open(snap, "w", encoding="utf-8"))
            apps = data.get("apps") if isinstance(data, dict) else None
            if not isinstance(apps, list):
                print(f"  ! {key}: no parseable apps list — skipped", flush=True)
                continue
            fetched[key] = (meta, apps)
            print(f"  + {key}: {len(apps)} entries", flush=True)
        except Exception as e:
            print(f"  ! {key}: fetch failed ({e}) — skipped", flush=True)
    return fetched


# ------------------------------------------------------------ normalize ----
def norm_date(s):
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
    t = " ".join(p for p in parts if p).strip()
    t = re.sub(r"\s+", " ", t)
    return t


def clean_name(name):
    """Strip uploader version suffixes like 'GeniePro_7.1.2' or 'Foo (2.0.0)'."""
    cleaned = re.sub(r"[\s_\-–.()]*v?\d+(\.\d+){1,3}[\s_\-–.()]*$", "", name).strip()
    return cleaned or name


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
                    size=int(str(a.get("size") or 0).strip() or 0)
                    if str(a.get("size") or "").strip().isdigit() else 0,
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
            vbundle = str(v.get("bundleIdentifier") or "").strip() or bundle
            text = clean_text(v.get("localizedDescription"), v.get("versionDescription"))
            if not text:
                text = base_text
            rec = dict(base)
            rec.update(bundle=vbundle,
                       version=str(v.get("version") or a.get("version") or "").strip(),
                       date=norm_date(v.get("date") or v.get("versionDate") or a.get("versionDate") or a.get("fullDate")),
                       text=text[:500], url=url)
            yield rec


# ------------------------------------------------------------ seed cache ---
def split_db(html_text):
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


def load_seed(path):
    """Seed maps from the previous index.html: name -> it/desc/tweak/sector/icon."""
    seed = {}
    if not os.path.exists(path):
        return seed, {}
    try:
        db = split_db(open(path, encoding="utf-8").read())
    except Exception as e:
        print(f"  ! seed parse failed: {e}", flush=True)
        return seed, {}
    for apps in db.get("cats", {}).values():
        for a in apps:
            seed[norm_name(a["name"])] = {"it": a.get("it"), "desc": a.get("desc"),
                                          "tweak": a.get("tweak"), "sector": a.get("sector"),
                                          "icon": a.get("icon")}
    print(f"  seed: {len(seed)} app cache entries from previous build", flush=True)
    return seed, db


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
                    "art": art.replace("100x100bb.jpg", "256x256bb.jpg") if art else "",
                }
        except Exception as e:
            print(f"  ! itunes batch failed: {e}", flush=True)
        time.sleep(1.0)
    return out


# ------------------------------------------------------------ assemble ----
def valid_bundle(b):
    return bool(b) and bool(BUNDLE_OK.match(b)) and not PLACEHOLDER_BUNDLE.match(b)


def norm_name(n):
    return re.sub(r"[^a-z0-9]", "", n.lower())


def extract_tweak(texts):
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
    """Distinct bundle ids, most common valid ones first."""
    cnt = Counter(r["bundle"] for r in recs if r["bundle"])
    valid = sorted([b for b in cnt if valid_bundle(b)], key=lambda b: -cnt[b])
    junk = sorted([b for b in cnt if not valid_bundle(b)], key=lambda b: -cnt[b])
    return valid + junk


def has_it(it):
    return bool(it and it.get("name"))


def build_db(fetched, seed, old_db):
    # Group by normalized app NAME (tweaked builds reuse junk/placeholder bundle
    # ids like com.karbinstoree across unrelated apps; the name is the stable
    # identity). All distinct bundle ids are collected into the bundle list.
    groups = defaultdict(list)
    for key, (meta, apps) in fetched.items():
        for rec in iter_builds(key, meta, apps):
            rec["src"] = key
            groups[norm_name(rec["name"])].append(rec)

    print(f"  grouped into {len(groups)} apps", flush=True)

    # which bundles need fresh App Store lookups? (seed is name-keyed)
    need = set()
    for gkey, recs in groups.items():
        if not has_it(seed.get(gkey, {}).get("it")):
            for b in bundle_rank(recs):
                need.add(b)
    print(f"  itunes lookups needed: {len(need)} (rest seeded)", flush=True)
    fresh_it = itunes_lookup(need) if need else {}

    def app_it(gkey, recs):
        s = seed.get(gkey, {}).get("it")
        if has_it(s):
            return s
        for b in bundle_rank(recs):
            it = fresh_it.get(b)
            if has_it(it):
                return it
        return None

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

        genre = (it or {}).get("g", "")
        sector = seedrec.get("sector")
        if not sector:
            low = (name + " " + " ".join(texts[:3])).lower()
            if any(k in low for k in AI_KEYWORDS):
                sector = "AI"
            else:
                sector = GENRE_SECTOR.get(genre, "Other")

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
                lst.append([r["version"], r["date"], r["url"]])
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

        desc = seedrec.get("desc")
        if not desc:
            bits = []
            if genre:
                bits.append(genre + " app")
            if tweak:
                bits.append("builds: " + tweak[:90])
            else:
                bits.append("community build")
            desc = " — ".join(bits) + f" · via {', '.join(SOURCES[s]['short'] for s in srcs)}"

        apps.append({
            "name": name, "desc": desc, "tweak": tweak,
            "bundle": bundles,
            "n": n, "nA": per.get("alans", 0), "nT": per.get("apptesters", 0),
            "per": per, "dev": dev, "devs": devs, "sector": sector,
            "icon": icon, "fb": fb, "it": it, "src": srcs,
            "upd": upd, "updDate": upd_date, "lv": latest["version"],
            "variants": variants, "dl": dl, "vh": vh,
            "minos": minos, "risk": risk,
        })

    apps.sort(key=lambda a: (-a["n"], a["name"].lower()))
    cats = {c: [] for c in CATS}
    for a in apps:
        cats[a["sector"] if a["sector"] in cats else "Other"].append(a)

    total = sum(a["n"] for a in apps)
    verified = sum(1 for a in apps if a["it"])
    toprated = sorted(
        [a for a in apps if a["it"] and a["it"]["c"] >= MIN_RATINGS_TOP],
        key=lambda a: (-a["it"]["r"], -a["it"]["c"]))[:12]

    repos = {k: {"entries": len(ma)} for k, (_, ma) in fetched.items()}
    db = {
        "cats": cats,
        "counts": {c: len(v) for c, v in cats.items()},
        "top": sorted(apps, key=lambda a: -a["n"])[:12],
        "toprated": toprated,
        "total": total,
        "unique": len(apps),
        "nsources": len(fetched),
        "repos": repos,
        "srcmeta": {k: {"label": SOURCES[k]["short"].upper(),
                        "cls": SOURCES[k]["cls"],
                        "official": SOURCES[k]["official"]}
                    for k in fetched},
        "refresh": (old_db.get("refresh") if old_db else None) or
                   {"dow": 1, "hh": 6, "mm": 37, "tz": "America/Chicago"},
    }
    return db, verified


# ------------------------------------------------------------ render -----
def esc_h(s):
    return html.escape(str(s), quote=True)


def render(db, verified, out_path):
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

    src_rows = "".join(
        f'  <div class="bid"><code>{esc_h(SOURCES[k]["url"])}</code>'
        f'<button class="cpy" data-c="{esc_h(SOURCES[k]["url"])}">COPY</button></div>\n'
        for k in db["repos"])
    dim_bits = []
    for k in db["repos"]:
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
        "{{HERO_LINE}}": f"{unique:,} unique hosts indexed · {total:,} strains analyzed · "
                         f"{pct}% verified against the App Store. Select a sector, run a query, "
                         f"sort by infection count — or skim the top rated.",
        "{{STAT_HOSTS}}": f"{unique:,}",
        "{{STAT_STRAINS}}": f"{total:,}",
        "{{STAT_SOURCES}}": str(nsrc),
        "{{TILES}}": f'    <div class="tiles">{tiles}</div>',
        "{{GUIDE_SRC_INTRO}}": (f"AltStore and SideStore can browse these {nsrc} repos directly. "
                                f"Go to the <b>Sources</b> tab, tap <b>+</b>, and paste these:"),
        "{{GUIDE_SRC_ROWS}}": src_rows + dim_line,
        "{{GUIDE_DISCLAIMER_SRC}}": (f"Every ⬇ GET button hands you a file from one of the "
                                     f"{nsrc} indexed repos listed below"),
        "{{SRC_OPTIONS}}": src_options.rstrip("\n"),
        "{{FOOTER_SOURCES}}": esc_h(" + ".join(l.lower() for l in labels)),
        "{{DATA_DATE}}": data_date,
    }
    for k, v in sub.items():
        assert k in head, f"placeholder {k} missing from template"
        head = head.replace(k, v)

    db_json = json.dumps(db, ensure_ascii=True)
    digest = hashlib.sha256(db_json.encode("utf-8")).hexdigest()[:16]
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(head)
        f.write(db_json)
        f.write(tail)
    print(f"  wrote {out_path} ({os.path.getsize(out_path):,} bytes), db hash {digest}")
    return digest


# ---------------------------------------------------------------- main -----
def main():
    offline = "--offline" in sys.argv
    out = "index.html"
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    out = os.path.join(HERE, out) if not os.path.isabs(out) else out

    print("fetching sources…", flush=True)
    fetched = load_sources(offline)
    if not fetched:
        sys.exit("no sources fetched — aborting")
    print("seeding from previous build…", flush=True)
    seed, old_db = load_seed(os.path.join(HERE, "index.html"))
    print("assembling DB…", flush=True)
    db, verified = build_db(fetched, seed, old_db)
    print(f"  unique={db['unique']:,} total builds={db['total']:,} "
          f"verified={verified:,}", flush=True)
    digest = render(db, verified, out)
    print(f"DONE db_hash={digest}")


if __name__ == "__main__":
    main()
