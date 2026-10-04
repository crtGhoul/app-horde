#!/usr/bin/env python3
"""VirusTotal verdict scanning for app-horde.

Reads the app DB from index.html (const DB = ...), resolves each app's newest
build download URL (same newest_dl_url() build.py uses at build time, so the
cache always lines up), and looks it up on VirusTotal via the vt.py skill CLI.

Maintains vt_cache.json in this directory, keyed by download URL:
  {"https://.../app.ipa": {"malicious": 0, "suspicious": 1, "harmless": 70,
   "undetected": 5, "total": 76, "scanned_date": "2026-10-02",
   "status": "scored"|"unscanned"}}

Usage:
  vt-scan.py lookup [--max-seconds N] [url]   backfill: lookup-only for
                                              uncached URLs (404 -> "unscanned")
  vt-scan.py check  [--max-seconds N] [url]   submit+poll for uncached URLs
  vt-scan.py monday [--max-seconds N]         delta mode: lookup uncached URLs,
                                              plus retry up to 20 previously-
                                              unscanned URLs via check

Free-tier limits: 4 requests/min, 500/day. Sleeps >=16s between CLI
invocations. On HTTP 429 prints "quota exhausted, resuming next run" and
exits 0. The cache is written after every URL, so any run is resume-safe.

Auth travels as an authd surrogate inside vt.py; this script never sees,
prints, or logs the API key.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build import split_db, newest_dl_url  # noqa: E402  (no side effects on import)

VT_CLI = os.environ.get("VT_CLI", "/home/hatch/workspace/skills/virustotal/bin/vt.py")
CACHE_PATH = os.path.join(HERE, "vt_cache.json")
DB_PATH = os.path.join(HERE, "index.html")
SLEEP_SECS = 16          # >=16s between CLI invocations (4 req/min limit)
MAX_UNSCANNED_RETRY = 20  # per monday run

_LAST_REQUEST_TIME = 0.0


def polite_sleep(min_interval=SLEEP_SECS):
    global _LAST_REQUEST_TIME
    now = time.monotonic()
    elapsed = now - _LAST_REQUEST_TIME
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _LAST_REQUEST_TIME = time.monotonic()


def load_cache():
    if not os.path.exists(CACHE_PATH):
        return {}
    if os.path.getsize(CACHE_PATH) == 0:
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError(f"Cache root must be a dict, got {type(data).__name__}")
        return data
    except Exception as exc:
        bak = f"{CACHE_PATH}.corrupt.{int(time.time())}"
        print(f"CRITICAL: Failed to read {CACHE_PATH}: {exc}", file=sys.stderr)
        print(f"Backing up corrupted file to {bak} and aborting to prevent cache wipe.", file=sys.stderr)
        try:
            import shutil
            shutil.copy2(CACHE_PATH, bak)
        except Exception:
            pass
        raise SystemExit(1)


def save_cache(cache, min_entries=0):
    if not isinstance(cache, dict):
        raise TypeError("save_cache: cache must be a dict")
    if min_entries > 0 and len(cache) < min_entries:
        raise RuntimeError(
            f"Cache size regression: refusing to overwrite {min_entries} entries with {len(cache)}"
        )
    dir_name = os.path.dirname(os.path.abspath(CACHE_PATH))
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix="vt_cache_", suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=True, sort_keys=True, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp_path, CACHE_PATH)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1 * (2 ** attempt))
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def app_urls():
    """Ordered unique newest-build URLs, most-built apps first."""
    db = split_db(open(DB_PATH, encoding="utf-8").read())
    apps = []
    for sec_apps in db.get("cats", {}).values():
        apps.extend(sec_apps)
    apps.sort(key=lambda a: (-(a.get("n") or 0), (a.get("name") or "").lower()))
    seen, urls = set(), []
    for a in apps:
        u = newest_dl_url(a)
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def cli(cmd, url):
    """Run the vt.py skill CLI. Returns (http_status, payload_dict)."""
    try:
        r = subprocess.run([sys.executable, VT_CLI, cmd, url],
                           capture_output=True, text=True, timeout=600)
    except Exception as e:
        return 0, {"cli_error": str(e)}
    try:
        out = json.loads(r.stdout or "{}")
    except Exception:
        return 0, {"cli_error": "unparseable CLI output", "rc": r.returncode}
    return out.get("http", 0), out.get("data") or {}


def extract_stats(payload):
    try:
        stats = payload["data"]["attributes"]["last_analysis_stats"] or {}
    except (KeyError, TypeError):
        return None
    total = sum(v for v in stats.values() if isinstance(v, (int, float)))
    if total == 0:
        # VT knows the URL but no engine has weighed in — effectively
        # unscanned; Monday check-mode retries will submit it for analysis.
        return None
    return {
        "malicious": int(stats.get("malicious") or 0),
        "suspicious": int(stats.get("suspicious") or 0),
        "harmless": int(stats.get("harmless") or 0),
        "undetected": int(stats.get("undetected") or 0),
        "total": int(total),
        "scanned_date": date.today().isoformat(),
        "status": "scored",
    }


def process(urls, cmd, cache, deadline, tag):
    """Process URLs with the given CLI command. Returns (done, quota_hit)."""
    done, fails = 0, 0
    initial_len = len(cache)
    for url in urls:
        if time.time() > deadline - 90:
            print(f"  [{tag}] time budget nearly spent, stopping cleanly "
                  f"({done}/{len(urls)} processed)", flush=True)
            break
        polite_sleep(SLEEP_SECS)
        http, payload = cli(cmd, url)
        if http == 429:
            print(f"  [{tag}] HTTP 429 received, backing off 60s...", flush=True)
            time.sleep(60)
            http, payload = cli(cmd, url)
            if http == 429:
                save_cache(cache, min_entries=initial_len)
                print("Daily quota exhausted, resuming next run", flush=True)
                return done, True
        if http in (0, 500, 502, 503, 504):
            fails += 1
            print(f"  [{tag}] Failure (HTTP {http}) on {url[:70]}… "
                  f"({fails} consecutive)", flush=True)
            if fails >= 3:
                print(f"  [{tag}] 3 consecutive failures, aborting run",
                      flush=True)
                save_cache(cache, min_entries=initial_len)
                sys.exit(1)
            time.sleep(5 * fails)
            continue
        fails = 0
        if http == 404:
            if cache.get(url, {}).get("status") != "scored":
                cache[url] = {"status": "unscanned",
                              "scanned_date": date.today().isoformat()}
            print(f"  [{tag}] unscanned {url[:70]}…", flush=True)
        elif http == 200:
            stats = extract_stats(payload)
            if stats:
                cache[url] = stats
                print(f"  [{tag}] scored {stats['malicious']}m/"
                      f"{stats['suspicious']}s/{stats['total']}t "
                      f"{url[:60]}…", flush=True)
            else:
                if cache.get(url, {}).get("status") != "scored":
                    cache[url] = {"status": "unscanned",
                                  "scanned_date": date.today().isoformat()}
                print(f"  [{tag}] no stats {url[:70]}…", flush=True)
        else:
            print(f"  [{tag}] http={http} {url[:70]}… (skipped)",
                  flush=True)
        save_cache(cache, min_entries=initial_len)
        done += 1
    return done, False


def main(argv):
    rest = []
    max_seconds = 0
    i = 1
    while i < len(argv):
        if argv[i] == "--max-seconds":
            i += 1
            try:
                max_seconds = int(argv[i])
            except (ValueError, IndexError):
                sys.exit("--max-seconds needs an integer")
        else:
            rest.append(argv[i])
        i += 1
    mode = rest[0] if rest else "lookup"
    if mode not in ("lookup", "check", "monday"):
        sys.exit("usage: vt-scan.py lookup|check|monday [--max-seconds N] [url]")
    only_url = rest[1] if len(rest) > 1 else None
    deadline = (time.time() + max_seconds) if max_seconds else float("inf")

    cache = load_cache()
    print(f"vt-scan mode={mode} cache={len(cache):,} urls", flush=True)

    if only_url:
        targets = {"lookup": [("lookup", [only_url])],
                   "check": [("check", [only_url])],
                   "monday": [("lookup", [only_url])]}[mode]
    elif mode == "monday":
        urls = app_urls()
        new = [u for u in urls if u not in cache]
        retry = [u for u, e in cache.items()
                 if isinstance(e, dict) and e.get("status") == "unscanned"]
        retry = [u for u in retry if u in set(urls)][:MAX_UNSCANNED_RETRY]
        print(f"  monday: {len(new)} new urls, {len(retry)} unscanned retries",
              flush=True)
        targets = [("lookup", new), ("check", retry)]
    else:
        urls = app_urls()
        todo = [u for u in urls if u not in cache]
        if not todo:
            print(f"backfill complete: all {len(urls):,} current urls cached",
                  flush=True)
            return 0
        print(f"  {mode}: {len(todo)} uncached urls of {len(urls):,} total",
              flush=True)
        targets = [(mode, todo)]

    for tag, urls in targets:
        done, quota_hit = process(urls, tag, cache, deadline, tag)
        print(f"  [{tag}] finished: {done} processed, "
              f"cache now {len(cache):,} urls", flush=True)
        if quota_hit:
            return 0
    save_cache(cache)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
