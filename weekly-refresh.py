#!/usr/bin/env python3
"""Monday refresh for app-horde: full rebuild (fresh source fetches) + push.

Run: python3 weekly-refresh.py
Designed to be run by the scheduled Monday cron. Aborts the push if the
rebuild fails or the sanity checks don't pass.
"""
import base64
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
import dynamic_credentials as dc

API = "https://api.github.com"
REPO = "crtGhoul/app-horde"


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "muse-github-skill",
                 "Content-Type": "application/json"}, method=method)
    dc.add_surrogate_to_request(req, "custom.github", entry_name="access_token",
                                allowed_hosts=["api.github.com"])
    try:
        return dc.read_json_response(urllib.request.urlopen(req))
    except urllib.error.HTTPError as exc:
        try:
            err = dc.read_json_response(exc)
        except Exception:
            err = {"message": f"HTTP {exc.code}"}
        raise SystemExit(f"API {method} {path} failed: {exc.code} {err}")


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
    idx = os.path.join(HERE, "index.html")
    s = open(idx, encoding="utf-8").read()
    assert len(s) > 1_000_000, "index.html suspiciously small"
    assert re.search(r"\d[\d,]* apps indexed", s), "hero line missing"
    js = re.findall(r"<script>(.*?)</script>", s, re.S)[0]
    open("/tmp/refresh-check.js", "w", encoding="utf-8").write(js)
    rc = subprocess.run(["node", "--check", "/tmp/refresh-check.js"]).returncode
    assert rc == 0, "JS syntax check failed"
    m = re.search(r"const DB = ", s)
    assert m, "DB missing"
    print("sanity checks passed", flush=True)

    # 3. push via git-database API (same route as the manual pushes)
    files = ["index.html", "fresh.xml", "build.py", "template_head.html",
             "template_tail.html", "weekly-refresh.py", "vt-scan.py",
             "vt_cache.json"]
    parent = api("GET", f"/repos/{REPO}/git/ref/heads/main")["object"]["sha"]
    entries = []
    for f in files:
        content = open(os.path.join(HERE, f), "rb").read()
        blob = api("POST", f"/repos/{REPO}/git/blobs",
                   {"content": base64.b64encode(content).decode(),
                    "encoding": "base64"})
        entries.append({"path": f, "mode": "100644", "type": "blob",
                        "sha": blob["sha"]})
    base_tree = api("GET", f"/repos/{REPO}/git/commits/{parent}")["tree"]["sha"]
    new_tree = api("POST", f"/repos/{REPO}/git/trees",
                   {"base_tree": base_tree, "tree": entries})["sha"]
    today = datetime.date.today().isoformat()
    commit = api("POST", f"/repos/{REPO}/git/commits",
                 {"message": f"Weekly refresh {today}\n\nAutomated Monday rebuild: "
                             f"fresh source fetches.",
                  "tree": new_tree, "parents": [parent]})
    api("PATCH", f"/repos/{REPO}/git/refs/heads/main", {"sha": commit["sha"]})
    print("PUSHED", commit["sha"][:12], flush=True)


if __name__ == "__main__":
    main()
