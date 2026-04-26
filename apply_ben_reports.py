"""
apply_ben_reports.py
Wires the two new Ben-designed ACP report handlers into:
  - octo_report_handlers.py  (get_handler routing)
  - octo_acp_worker.py       (_get_report_type routing + per-type pricing)

Run once:
    python apply_ben_reports.py

Safe to re-run -- detects if already applied.
"""

from pathlib import Path

ROOT = Path(__file__).parent

# ── Patch 1: octo_report_handlers.py -- add to get_handler() ──────────────────

HANDLERS_FILE = ROOT / "octo_report_handlers.py"

HANDLERS_IMPORT = (
    "from octo_acp_ben_reports import handle_grok_sentiment_brief, handle_fear_crowd_divergence\n"
)

HANDLERS_ROUTING = """\
    if any(k in t for k in ["grok_sentiment", "grok_brief", "x_sentiment", "twitter_sentiment"]):
        return handle_grok_sentiment_brief
    if any(k in t for k in ["divergence", "fear_crowd", "crowd_divergence", "fear_vs_crowd"]):
        return handle_fear_crowd_divergence
"""

# Anchor: insert routing just before the final fallback return
HANDLERS_ANCHOR = "    return handle_crypto_market_signal"


def patch_handlers():
    content = HANDLERS_FILE.read_text(encoding="utf-8")

    # Already applied?
    if "handle_grok_sentiment_brief" in content:
        print(f"[OK] {HANDLERS_FILE.name}: already patched")
        return

    # Add import at top (after first 'from octo_report_handlers import' style line or after imports block)
    if HANDLERS_IMPORT.strip() not in content:
        # Insert after the last 'import' line in the imports block
        lines = content.split("\n")
        last_import_idx = 0
        for i, line in enumerate(lines):
            if line.startswith("import ") or line.startswith("from "):
                last_import_idx = i
        lines.insert(last_import_idx + 1, HANDLERS_IMPORT.rstrip())
        content = "\n".join(lines)

    # Add routing before final fallback
    if HANDLERS_ANCHOR not in content:
        print(f"[WARN] {HANDLERS_FILE.name}: anchor not found -- check file manually")
        return

    content = content.replace(HANDLERS_ANCHOR, HANDLERS_ROUTING + HANDLERS_ANCHOR)
    HANDLERS_FILE.write_text(content, encoding="utf-8")
    print(f"[OK] {HANDLERS_FILE.name}: patched -- grok_sentiment_brief + fear_crowd_divergence registered")


# ── Patch 2: octo_acp_worker.py -- routing + per-type pricing ─────────────────

WORKER_FILE = ROOT / "octo_acp_worker.py"

WORKER_ROUTING = """\
    if any(k in all_text for k in ["grok sentiment", "x sentiment", "grok_sentiment", "twitter sentiment"]):
        return "grok_sentiment_brief"
    if any(k in all_text for k in ["divergence", "fear crowd", "crowd divergence", "fear vs crowd"]):
        return "fear_crowd_divergence"
"""

# Anchor: insert routing just before final return in _get_report_type
WORKER_ROUTING_ANCHOR = '    return "market_signal"'

WORKER_PRICING_OLD = '        "--amount",   str(ACP_PRICE_USDC),'
WORKER_PRICING_NEW = '''\
        "--amount",   str(2.0 if report_type == "fear_crowd_divergence" else ACP_PRICE_USDC),'''


def patch_worker():
    content = WORKER_FILE.read_text(encoding="utf-8")

    # Already applied?
    if "grok_sentiment_brief" in content:
        print(f"[OK] {WORKER_FILE.name}: already patched")
        return

    # Add routing
    if WORKER_ROUTING_ANCHOR not in content:
        print(f"[WARN] {WORKER_FILE.name}: routing anchor not found -- check file manually")
    else:
        content = content.replace(WORKER_ROUTING_ANCHOR, WORKER_ROUTING + WORKER_ROUTING_ANCHOR, 1)
        print(f"[OK] {WORKER_FILE.name}: routing added")

    # Per-type pricing
    if WORKER_PRICING_OLD not in content:
        print(f"[WARN] {WORKER_FILE.name}: pricing anchor not found -- set-budget price unchanged")
    else:
        content = content.replace(WORKER_PRICING_OLD, WORKER_PRICING_NEW)
        print(f"[OK] {WORKER_FILE.name}: per-type pricing added ($2 for divergence, $1 for rest)")

    WORKER_FILE.write_text(content, encoding="utf-8")


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Applying Agent_Ben ACP report patches...\n")
    patch_handlers()
    patch_worker()

    # Verify syntax
    import ast, sys
    errors = []
    for f in [HANDLERS_FILE, WORKER_FILE]:
        try:
            ast.parse(f.read_text(encoding="utf-8"))
            print(f"[OK] {f.name}: syntax OK")
        except SyntaxError as e:
            print(f"[FAIL] {f.name}: syntax error -- {e}")
            errors.append(f.name)

    if errors:
        print(f"\n[!] Syntax errors in: {errors}. Restore from backup if needed.")
        sys.exit(1)
    else:
        print("\nAll patches applied successfully.")
        print("Restart the ACP worker: schtasks /Run /TN Octodamus-ACP-Worker")
