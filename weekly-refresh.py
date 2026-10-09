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
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # sibling import: gh_push
import gh_push


def sanity_check(path):
    """Gate the push on a plausible index.html: big enough, hero line and
    the embedded DB present, and the inline <script> parses as JS.

    Raises AssertionError (which aborts the refresh before any push) on
    any failure.
    """
    s = open(path, encoding="utf-8").read()
    assert len(s) > 1_000_000, "index.html suspiciously small"
    assert re.search(r"\d[\d,]* apps indexed", s), "hero line missing"
    assert "const DB = " in s, "DB missing"
    scripts = re.findall(r"<script>(.*?)</script>", s, re.S)
    assert scripts, "no <script> blocks found in index.html"
    check_js = "/tmp/refresh-check.js"
    open(check_js, "w", encoding="utf-8").write(scripts[0])
    rc = subprocess.run(["node", "--check", check_js]).returncode
    assert rc == 0, "JS syntax check failed"
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

    # 3. push via git-database API (same route as the manual pushes)
    files = ["index.html", "fresh.xml", "build.py", "template_head.html",
             "template_tail.html", "weekly-refresh.py", "vt-scan.py",
             "vt_cache.json"]
    today = datetime.date.today().isoformat()
    commit_sha, _ = gh_push.push_tree(
        HERE, files,
        f"Weekly refresh {today}\n\nAutomated Monday rebuild: "
        f"fresh source fetches.",
        branch="main", log=lambda *a: None)
    print("PUSHED", commit_sha[:12], flush=True)


if __name__ == "__main__":
    main()
