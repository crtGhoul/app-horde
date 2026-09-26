# APP HORDE 🧟

A searchable index of sideloadable iOS apps — zombie-terminal styling, App Store verification, risk signals, version history, and a beginner field manual. Static site, no backend, no signup.

Live: https://crtghoul.github.io/app-horde/

## How it works

`index.html` is **generated** by `build.py`. Don't hand-edit the built page — edit the templates and rebuild:

- `template_head.html` — markup/CSS up to the embedded database
- `template_tail.html` — all the JavaScript after the database
- `build.py` — downloads every source, dedupes, verifies against the App Store, renders `index.html`

## Build

```bash
python3 build.py
```

Downloads all 12 sources (~35MB), looks up new bundle IDs on the Apple Search API, and writes `index.html`. Takes ~2 minutes (Apple API rate limits).

```bash
python3 build.py --offline        # reuse sources/*.json snapshots, still queries Apple for new bundles
python3 build.py --out /tmp/test.html
```

Two runs against unchanged snapshots produce byte-identical output (deterministic build).

## Sources indexed

| Source | What it is |
|---|---|
| Alan's Gigantic Repo | the big community IPA library |
| AppTesters IPA Repo | the other big community IPA library |
| CyPwn | community IPA repo |
| Quantum | sideloading source |
| Quantum Plus | sideloading source |
| WuXu's AppLibrary | personal IPA library |
| SideStore Community Source | SideStore community apps |
| AltStore Official | official AltStore apps ★ |
| OatmealDome | DolphiniOS developer source ★ |
| UTM | UTM developer source ★ |
| iSH | iSH developer source ★ |
| Flycast | Flycast developer source ★ |

★ = official developer source — straight from the app maker. Starfiles is configured but currently unreachable (Cloudflare block); the builder skips it gracefully.

## Weekly refresh

The "feeds every Monday" countdown on the site is real: `monday-refresh.workflow.yml` is a GitHub Actions workflow. Because automation files can't be pushed via API, install it once by hand:

1. On GitHub, create `.github/workflows/refresh.yml` in this repo
2. Paste the contents of `monday-refresh.workflow.yml`
3. Done — it runs every Monday ~09:00 UTC, rebuilds `index.html`, and commits only when the index actually changed

## Notes

- Apps are grouped by name (tweaked builds reuse junk bundle IDs across unrelated apps, so the name is the stable identity). Every distinct bundle ID is kept in the app's bundle list.
- App Store verification = the bundle ID exists on Apple's Search API. "Unverified" doesn't always mean fake — legit betas and delisted apps won't match either.
- `sources/*.json` snapshots are gitignored build inputs, not committed.

## Disclaimer

Unofficial builds — do your own research, use at your own risk. This is an index that
links to third-party repos; it hosts no files. Corrections or removal requests:
[crtghoul on GitHub](https://github.com/crtGhoul).

---
Coded by Strider (Muse). Check out [Muse](https://muse.ai/join) — redeem code `MG9C6Z` in Settings within 48 hours of joining and we both get 1 billion Muse tokens.
