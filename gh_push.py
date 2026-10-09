#!/usr/bin/env python3
"""Shared git-database push helpers for app-horde.

`git push` cannot authenticate on this VM, so pushes go through GitHub's
git-database API: blobs -> tree -> commit -> PATCH refs/heads/<branch>.

Used by:
  push-main.py       manual pushes: push-main.py "<message>" <file> [file ...]
  weekly-refresh.py  the Monday cron's final step (fixed file list + message)

The API call sequence here is identical to the old inline copies in those
scripts (same endpoints, same payload shapes); only the logging verbosity
differs per caller via the `log` parameter.

Auth travels as an authd surrogate inside dynamic_credentials; this module
never sees, prints, or logs the token.

Importing this module has no side effects beyond loading the credential
helper.
"""
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
import dynamic_credentials as dc

API = "https://api.github.com"
ALLOWED = ["api.github.com"]
REPO = "crtGhoul/app-horde"


def api(method, path, body=None):
    """One GitHub API call with the surrogate credential.

    Dies with SystemExit (loud, with the API's error message) on HTTP
    errors so cron/manual runs fail visibly instead of half-pushing.
    """
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "muse-github-skill",
                 "Content-Type": "application/json"},
        method=method)
    dc.add_surrogate_to_request(req, "custom.github", entry_name="access_token",
                                allowed_hosts=ALLOWED)
    try:
        return dc.read_json_response(urllib.request.urlopen(req))
    except urllib.error.HTTPError as exc:
        try:
            err = dc.read_json_response(exc)
        except Exception:
            err = {"message": f"HTTP {exc.code}"}
        raise SystemExit(f"API {method} {path} failed: {exc.code} {err}")


def blob_sha(content: bytes) -> str:
    """Git blob SHA-1 of content — the value a remote tree entry must match."""
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def push_tree(workdir, files, message, branch="main", author=None, log=print):
    """Upload blobs -> build tree -> commit -> move <branch> to the commit.

    `files` are paths relative to `workdir`. `author`, when given, is
    passed through as the commit's author block (push-main.py attributes
    commits to Strider2; weekly-refresh.py passes None, exactly like its
    old inline code, so GitHub attributes those to the token owner).

    Returns (commit_sha, new_tree_sha). Raises SystemExit on API errors.
    """
    parent = api("GET", f"/repos/{REPO}/git/ref/heads/{branch}")["object"]["sha"]
    log(f"remote {branch} tip: {parent}")
    entries = []
    for f in files:
        with open(os.path.join(workdir, f), "rb") as fh:
            content = fh.read()
        blob = api("POST", f"/repos/{REPO}/git/blobs",
                   {"content": base64.b64encode(content).decode(),
                    "encoding": "base64"})
        entries.append({"path": f, "mode": "100644", "type": "blob",
                        "sha": blob["sha"]})
        log(f"  blob {f} ({len(content):,} bytes) -> {blob['sha'][:12]}")
    base_tree = api("GET", f"/repos/{REPO}/git/commits/{parent}")["tree"]["sha"]
    new_tree = api("POST", f"/repos/{REPO}/git/trees",
                   {"base_tree": base_tree, "tree": entries})["sha"]
    log(f"new tree: {new_tree}")
    payload = {"message": message, "tree": new_tree, "parents": [parent]}
    if author:
        payload["author"] = author
    commit = api("POST", f"/repos/{REPO}/git/commits", payload)
    log(f"new commit: {commit['sha']}")
    api("PATCH", f"/repos/{REPO}/git/refs/heads/{branch}",
        {"sha": commit["sha"]})
    log(f"pushed {REPO} {branch} -> {commit['sha']}")
    return commit["sha"], new_tree


def byte_verify(workdir, files, new_tree, log=print):
    """Confirm every remote tree blob SHA matches the local file bytes.

    Raises SystemExit on mismatch — the push went through but the remote
    tree is not what we uploaded, so nothing else should push on top until
    that's investigated.
    """
    remote = api("GET",
                 f"/repos/{REPO}/git/trees/{new_tree}?recursive=1")["tree"]
    rmap = {e["path"]: e["sha"] for e in remote if e["type"] == "blob"}
    ok = True
    for f in files:
        with open(os.path.join(workdir, f), "rb") as fh:
            want = blob_sha(fh.read())
        got = rmap.get(f)
        match = got == want
        ok = ok and match
        log(f"  verify {f}: {'OK ' if match else 'MISMATCH'} "
            f"local={want[:12]} remote={(got or '?')[:12]}")
    if not ok:
        raise SystemExit("BYTE VERIFICATION FAILED")
    log("byte verification passed: remote tree == local files")
