#!/usr/bin/env python3
"""Push app-horde working-tree files straight to main via the git-database API.

Usage: push-main.py <message> <file> [file ...] [--delete <file> ...]
Files are paths relative to ~/workspace/app-horde-work.
`git push` cannot authenticate on this VM, so this does blobs -> tree ->
commit -> PATCH refs/heads/main, then byte-verifies the remote tree.

--delete removes paths from the repo tree (git-rm equivalent).

Refuses to touch .github/workflows/* (PAT lacks the Workflows permission).

The API plumbing lives in gh_push.py (shared with weekly-refresh.py).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling import: gh_push
import gh_push

BRANCH = "main"
WORKDIR = "/home/hatch/workspace/app-horde-work"
AUTHOR = {"name": "Strider2", "email": "strider2@users.noreply.github.com"}


def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: push-main.py <message> <file> [file ...] "
                         "[--delete <file> ...]")
    message = sys.argv[1]
    rest = sys.argv[2:]
    files, deletes = [], []
    target = files
    for a in rest:
        if a == "--delete":
            target = deletes
            continue
        target.append(a)
    if not files and not deletes:
        raise SystemExit("nothing to push")
    if any(f.startswith(".github/workflows/") for f in files + deletes):
        raise SystemExit("refusing: file list touches .github/workflows/*")
    for f in files:
        if not os.path.isfile(os.path.join(WORKDIR, f)):
            raise SystemExit(f"missing local file: {f}")

    commit_sha, new_tree = gh_push.push_tree(WORKDIR, files, message,
                                             branch=BRANCH, author=AUTHOR,
                                             deletes=deletes)
    gh_push.byte_verify(WORKDIR, files, new_tree, deletes=deletes)


if __name__ == "__main__":
    main()
