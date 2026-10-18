#!/usr/bin/env python3
"""Monday refresh for app-horde: full rebuild (fresh source fetches) + push.

Run: python3 weekly-refresh.py
Designed to be run by the scheduled Monday cron. Aborts the push if the
rebuild fails or the sanity checks don't pass.

The git-database push plumbing lives in gh_push.py (shared with
push-main.py); this script passes a null logger to it because the cron's
push step has always been quiet apart from the final "PUSHED" line.
"""
import datetime
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # sibling import: gh_push
import gh_push


def sanity_check(path):
    """Gate the push on a plausible split build: a small index.html shell,
    the data/*.json dataset present and parseable, every output file under
    Cloudflare Pages' 25 MiB per-file limit, and every inline <script>
    parsing as JS.

    Raises AssertionError (which aborts the refresh before any push) on
    any failure.
    """
    s = open(path, encoding="utf-8").read()
    assert len(s) > 50_000, "index.html suspiciously small"
    assert len(s) < 25 * 1024 * 1024, \
        "index.html exceeds Cloudflare Pages' 25 MiB per-file limit"
    assert re.search(r"\d[\d,]* apps indexed", s), "hero line missing"
    assert "loadDB()" in s, "dataset loader missing from index.html"
    data_dir = os.path.join(os.path.dirname(path), "data")
    meta_p = os.path.join(data_dir, "meta.json")
    assert os.path.isfile(meta_p), "data/meta.json missing"
    meta = json.load(open(meta_p, encoding="utf-8"))
    assert meta.get("catfiles"), "data/meta.json has no catfiles map"
    assert meta.get("unique", 0) > 1000, "meta.json app count implausible"
    for sector, fname in meta["catfiles"].items():
        fp = os.path.join(data_dir, fname)
        assert os.path.isfile(fp), f"data/{fname} missing"
        assert os.path.getsize(fp) < 25 * 1024 * 1024, \
            f"data/{fname} exceeds Cloudflare Pages' 25 MiB per-file limit"
        apps = json.load(open(fp, encoding="utf-8"))
        assert isinstance(apps, list) and apps, f"data/{fname} empty/not a list"
    scripts = re.findall(r"<script>(.*?)</script>", s, re.S)
    assert scripts, "no <script> blocks found in index.html"
    check_js = "/tmp/refresh-check.js"
    for i, blk in enumerate(scripts):
        if not blk.strip():
            continue
        open(check_js, "w", encoding="utf-8").write(blk)
        rc = subprocess.run(["node", "--check", check_js]).returncode
        assert rc == 0, f"JS syntax check failed on script block {i}"
    print("sanity checks passed", flush=True)


def main():
    # 0. VirusTotal verdict scan (best-effort; deltas only — never blocks the
    #    refresh). Verdicts land in vt_cache.json, which build.py bakes into
    #    the DB. The scan is resume-safe, so an interrupted run just
    #    continues next Monday.
    r = subprocess.run([sys.executable, "vt-scan.py", "monday",
                        "--max-seconds", "5400"],
                       cwd=HERE, capture_output=True, text=True, timeout=7200)
    print(r.stdout[-1500:], flush=True)
    if r.returncode != 0:
        print("VT SCAN WARNING (continuing anyway):", r.stderr[-500:],
              flush=True)

    # 1. full rebuild with fresh source fetches
    r = subprocess.run([sys.executable, "build.py"], cwd=HERE,
                       capture_output=True, text=True, timeout=5400)
    print(r.stdout[-2000:], flush=True)
    if r.returncode != 0:
        print("BUILD FAILED", flush=True)
        print(r.stderr[-2000:], flush=True)
        sys.exit(1)

    # 2. sanity checks before pushing
    sanity_check(os.path.join(HERE, "index.html"))

    # 3. push via git-database API (same route as the manual pushes).
    # The split dataset (data/meta.json + data/cats/*.json) ships with the
    # shell — GitHub Pages and the Cloudflare mirror both need it.
    data_files = sorted(
        os.path.relpath(p, HERE)
        for p in glob.glob(os.path.join(HERE, "data", "meta.json"))
        + glob.glob(os.path.join(HERE, "data", "cats", "*.json")))
    assert data_files, "no data files built — aborting push"
    files = ["index.html", "fresh.xml", "build.py", "template_head.html",
             "template_tail.html", "weekly-refresh.py", "vt-scan.py",
             "vt_cache.json", "curated.json", "link_health.json"] + data_files
    today = datetime.date.today().isoformat()
    commit_sha, _ = gh_push.push_tree(
        HERE, files,
        f"Weekly refresh {today}\n\nAutomated Monday rebuild: "
        f"fresh source fetches.",
        branch="main", log=lambda *a: None)
    print("PUSHED", commit_sha[:12], flush=True)


if __name__ == "__main__":
    main()
