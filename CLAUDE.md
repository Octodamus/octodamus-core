# CLAUDE.md — Octodamus
# Loaded every session. See .claude/rules/ for detailed rules.

## Identity
- Project: Octodamus — AI-powered market intelligence platform
- Owner: @octodamusai on X
- Stack: Python, Windows 11, Task Scheduler, Telegram bot, X/Twitter poster, Cloudflare tunnel
- Working dir: C:\Users\walli\octodamus
- Site repo: C:\Users\walli\octodamus-site

---

## Hard Rules

**Verify date/time from system clock — never infer from context.**
  python -c "from datetime import datetime; print(datetime.now().strftime('%A, %B %d %Y %I:%M %p'))"

**octodamus.com deploys via GitHub push only.**
Never suggest manual upload, SFTP, or Cloudflare dashboard.

**Math accuracy is mandatory.**
Tax on gains only, not total value.
  CORRECT: 1 + (10.9 - 1) × 0.63 = 6.67x kept
  WRONG:   10.9 × 0.63 = 6.87x kept
Omit any calculation you're unsure about rather than guess.

---

## Claude Code Tips

**`ultrathink`** — Append to any message to trigger extended thinking. Use for complex bugs, signal wiring, or architecture decisions. No meaningful token cost difference; use freely.

**Plan mode** — `Shift+Tab` before making large changes. Claude presents a plan for approval before touching code.

**Background tasks** — "Run the runner in the background" during debug sessions. Claude keeps the process alive and reads logs directly.

**Context7 MCP** — When integrating a new API/SDK, say "use Context7 for [library] docs" and it pulls the latest compressed documentation automatically. MCP is wired in `~/.claude/settings.json`.

---

## See Also
- `.claude/rules/architecture.md` — key files, tasks, data files, deployment
- `.claude/rules/coding.md`       — coding style, Windows gotchas
- `.claude/rules/botcoin.md`      — BOTCOIN dashboard + mining rules
- `.claude/rules/signals.md`      — aviation volume + calibration signal details
- `.claude/rules/distro.md`       — Octo Distro Media: 10 free tools, subscriber capture, MCP sales engine
- `.claude/rules/future.md`       — $OCTO token, roadmap notes
- `octodamus-site/CLAUDE.md`      — site-specific deploy rules
