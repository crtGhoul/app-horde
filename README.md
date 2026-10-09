# APP HORDE 🧟

A searchable index of sideloadable iOS apps — zombie-terminal styling (intentional), App Store verification, risk signals, version history, and a beginner field manual. Static site, no backend, no signup. It hosts no files; it indexes third-party repos and links out.

Live: https://crtghoul.github.io/app-horde/ — repo `crtGhoul/app-horde`, GitHub Pages.

## How the pipeline works

`index.html` is **generated** — never hand-edit it. Edit a template, rebuild, sanity-check, push.

```
sources/*.json          snapshots of each repo feed (~35MB, gitignored)
   └─ build.py          fetch → dedupe → verify → bake DB → render
        ├─ templates: template_head.html (markup/CSS up to the embedded DB)
        │             template_tail.html (JS after the DB: search, modal, one-tap installers)
        ├─ Apple Search API: only NEW bundle IDs are looked up, in batches of 50.
        │  Verification results are seeded from the previous index.html's DB,
        │  so repeat builds are cheap.
        ├─ vt_cache.json: VirusTotal verdicts keyed by download URL, baked in
        │  (see VirusTotal below)
        ├─ index.html    the whole site — one self-contained file with the DB embedded
        └─ fresh.xml     RSS 2.0 feed of builds ≤7 days old
```

Builds are deterministic: identical snapshots → byte-identical `index.html`.

## Build

```bash
python3 build.py                # full run: fresh downloads of all sources, ~2 min (Apple rate limits)
python3 build.py --offline      # reuse sources/*.json snapshots instead of downloading (testing)
python3 build.py --out /tmp/test.html
```

A source that fails to fetch is skipped gracefully — one dead repo never breaks the build.

## Sources indexed (13)

| Source | Type |
|---|---|
| Alan's Gigantic Repo | community IPA library |
| AppTesters IPA Repo | community IPA library |
| CyPwn IPA Library | community IPA library |
| Quantum Source / Quantum Plus | sideloading sources |
| WuXu's Library++ | personal IPA library |
| Starfiles | community IPA repo — currently failing to fetch, skipped automatically (the builder tolerates dead sources) |
| SideStore Community | official-ish community source ★ |
| AltStore Official ★ | from the app maker |
| OatmealDome (DolphiniOS) ★ | developer source |
| UTM ★ | developer source |
| iSH ★ | developer source |
| Flycast ★ | developer source |

★ = `official=True` in the build — straight from the app maker (or its official community source). The site also shows per-build source badges so you can see which repo each download link came from.

## VirusTotal verdicts

Each app card shows a VirusTotal line for its newest build ("N engines flagged this build as malicious" in red, or clean).

- `vt_cache.json` — verdicts keyed by download URL: `{malicious, suspicious, harmless, undetected, total, scanned_date, status}`. Committed, so the site carries verdicts even when the API is idle.
- `vt-scan.py` — maintains the cache; auth rides inside the vt.py skill CLI, the key never appears here. Free-tier limits (4 req/min, 500/day, sleeps ≥16s between calls); quota exhaustion prints "quota exhausted, resuming next run" and exits 0. Resume-safe: the cache is written after every URL.

```bash
python3 vt-scan.py lookup [--max-seconds N] [url]   # backfill: lookup uncached URLs (404 -> "unscanned")
python3 vt-scan.py check  [--max-seconds N] [url]   # submit + poll for uncached URLs
python3 vt-scan.py monday [--max-seconds N]         # delta mode: lookup uncached + retry up to 20
                                                    #   previously-unscanned URLs via check
```

The same script is shared with a daily VirusTotal backfill cron.

## Weekly refresh

`weekly-refresh.py` is the Monday pipeline. It runs, in order:

1. **VT scan** — `vt-scan.py monday --max-seconds 5400` (best-effort; deltas only, never blocks the refresh on failure).
2. **Rebuild** — full `build.py` with fresh source fetches.
3. **Sanity checks** — aborts the push if any fail:
   - `index.html` > 1MB and contains the "N apps indexed" hero line and the embedded DB
   - extracted `<script>` passes `node --check` (JS syntax)
4. **Push** — pushes `index.html`, `fresh.xml`, `build.py`, `template_head.html`, `template_tail.html`, `weekly-refresh.py`, `vt-scan.py`, `vt_cache.json` via the git-database API.

Run it: `python3 weekly-refresh.py`

There's also `monday-refresh.workflow.yml` — an optional GitHub Actions workflow that rebuilds and commits only when the index changed. Because the API token can't write `.github/workflows/*`, installing it is a one-time manual step (create `.github/workflows/refresh.yml` in the repo on github.com, paste the file in).

## Pushing

`git push` can't authenticate on this VM. The canonical push path is the git-database API:

```bash
python3 push-main.py "<commit message>" <file> [file ...]
```

- Blobs → tree → commit → PATCH `refs/heads/main`, then **byte-verifies** the remote tree against local files.
- Refuses anything under `.github/workflows/*` (token lacks the Workflows permission — install workflows by hand).
- Never push production directly with a generic git push even if auth starts working; this is the audited path.

⚠️ `DO_NOT_USE_gh-push-apphorde.py` is **stale — never use it**. It targets an old `devin/app-horde-expansion-2026-09-26` branch and refuses to run (hard `SystemExit` guard).

## File layout

| File | What it is |
|---|---|
| `index.html` | the site. Generated. Never hand-edit. |
| `template_head.html` | markup + CSS up to the embedded DB |
| `template_tail.html` | all JS after the DB (search, modal, favorites, one-tap installer deep-links: AltStore, SideStore, TrollStore, Feather, LiveContainer — Scarlet has no documented install scheme and is deliberately omitted) |
| `build.py` | fetcher + deduper + verifier + renderer |
| `vt-scan.py` | VirusTotal cache maintenance |
| `vt_cache.json` | VT verdicts, committed |
| `weekly-refresh.py` | Monday pipeline: VT scan → build → sanity checks → push |
| `push-main.py` | canonical production push (git-database API, byte-verified) |
| `monday-refresh.workflow.yml` | optional GitHub Actions weekly rebuild (install by hand) |
| `fresh.xml` | RSS of fresh drops. Generated. |
| `sources/*.json` | downloaded snapshots, gitignored, not committed |
| `gh_push.py` | shared GitHub git-database plumbing used by push-main.py + weekly-refresh.py |
| `DO_NOT_USE_gh-push-apphorde.py` | stale, quarantined. Never use. |

## Notes

- Apps are grouped by **name** (tweaked builds reuse junk bundle IDs across unrelated apps, so the name is the stable identity). Every distinct bundle ID is kept in the app's bundle list.
- App Store verification = the bundle ID exists on Apple's Search API. "Unverified" doesn't always mean fake — legit betas and delisted apps won't match either.
- The site's favorites, the beginner field manual, and the category browser are all client-side; there's no server to deploy.

## Disclaimer

Unofficial builds — do your own research, use at your own risk. This is an index that
links to third-party repos; it hosts no files. Corrections or removal requests:
[crtghoul on GitHub](https://github.com/crtGhoul).

---
Coded by Strider (Muse). Check out [Muse](https://muse.ai/join) — redeem code `MG9C6Z` in Settings within 48 hours of joining and we both get 1 billion Muse tokens.
