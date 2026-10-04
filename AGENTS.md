# AGENTS.md — App-Horde

Binding conventions for any agent working in this project.

## Scope & Project
- **Repo:** `crtGhoul/app-horde`
- **Path:** `C:\Users\velaz\app-horde`
- **Description:** Fast, lightweight web catalog/index site for iOS apps and IPAs, generated via static build scripts and VirusTotal scans.

## Landmines (Learned the hard way)
1. **Cache integrity:** `vt_cache.json` must stay clean and deterministic; never corrupt cached scan hashes or blow away rate-limited API results.
2. **Static generation:** Python scripts (`build.py`, `weekly-refresh.py`) generate the final `index.html` from `template_head.html` and `template_tail.html`; never edit compiled output directly if templates govern it.
3. **iOS Safari compatibility:** Links, plist manifests, and direct download buttons must conform to Safari handling without triggering broken protocol errors.

## Anti-Slop Filter
1. **Honesty first:** No fabricated ratings, fake review stars, or invented download counters.
2. **Clean layout:** Quiet by default, no spammy badge clutter.

## Your Edge (Windows Local Agent)
- You run on the real Windows PC.
- Run the build scripts for real (`python build.py`), test HTML rendering with Playwright emulating iPhone viewports, and verify link integrity.
- Label everything **VERIFIED** vs **LOGIC-ONLY** (plus what would prove it).

## Operating Rules
- Laziest correct solution wins (reuse > stdlib > native > new dependency).
- Minimal diffs.
- Never cut tests, validation, or accessibility.
- Never cut a release or git push unless explicitly instructed.
- Report: files changed + commit, VERIFIED vs LOGIC-ONLY, known gaps (<=300 words unless VERBOSE=1).
