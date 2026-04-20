# HyperFrames Video — Octodamus

## Overview
HyperFrames is installed and ready. Octodamus uses it to produce YouTube videos via HTML+GSAP compositions rendered to MP4.

## File Locations
- Skills:       `C:\Users\walli\octodamus-site\.agents\skills\` (hyperframes, hyperframes-cli, gsap, etc.)
- Video projects: `C:\Users\walli\octodamus-site\videos\`
- DESIGN.md:    `C:\Users\walli\octodamus-site\videos\DESIGN.md` (brand identity — read before every video)
- YouTube uploader: `octo_youtube.py`
- Python wrapper:   `octo_hyperframes.py`

## Workflow

1. **Plan** — `python octo_ceo.py video [topic]` generates a brief + scene plan
2. **Scaffold** — `cd C:\Users\walli\octodamus-site\videos && npx hyperframes init [project-name] --non-interactive`
3. **Design** — copy `DESIGN.md` into project folder, write `index.html` composition
4. **Lint** — `npx hyperframes lint`
5. **Preview** — `npx hyperframes preview` (opens browser studio, hot reload)
6. **Render** — `npx hyperframes render --quality standard`
7. **Upload** — `python octo_youtube.py upload [mp4-path] --title "..." --description "..."`

## CEO Video Mode CLI
```
python octo_ceo.py video [topic]        # generate brief + scene plan
python octo_hyperframes.py scaffold [name]  # init project with Octodamus DESIGN.md
python octo_hyperframes.py render [path]    # render to MP4
python octo_youtube.py upload [mp4]         # upload to YouTube channel
```

## HyperFrames Key Rules (from SKILL.md)
- ALWAYS read `DESIGN.md` before writing any HTML
- Build end-state layout first (static CSS), THEN add GSAP animations
- Use `gsap.from()` for entrances, `gsap.to()` for exits
- Register every timeline: `window.__timelines["composition-id"] = tl`
- `data-composition-id` on root div is mandatory
- `data-start` and `data-duration` control timing
- Lint before render — catches missing attributes, overlapping tracks
- Chrome headless: `C:\Users\walli\.cache\hyperframes\chrome\...`
- Memory warning: 1.7 GB free — use `--quality draft` while iterating, `--workers 2` if render fails

## Video Content Strategy
- **Signal Breakdowns** — "Why BTC just gave a confluence signal" (30-60s)
- **Oracle Call Reveals** — timestamped prediction + outcome (15-30s)
- **Weekly Scorecard** — win/loss recap with stats (60-90s)
- **Market Regime** — macro setup read (30-60s)
- Post schedule: 2-3x/week, optimize for YouTube Shorts first (1080x1920)

## YouTube Channel
- Channel: Octodamus (@octodamusai)
- Upload via: `octo_youtube.py` (YouTube Data API v3)
- OAuth credentials: `gdrive_credentials.json` (same Google account)
- Quota: 10,000 units/day free tier; one upload = ~1,600 units

## Environment Check
```
npx hyperframes doctor   # verify Chrome, FFmpeg, Node
npx hyperframes upgrade  # keep current
```
