"""
octo_agent_db.py — Octodamus Agent Visitor & Customer Database

Tracks every AI agent that visits api.octodamus.com:
- Visitor log (IP, user-agent, endpoints, timestamps)
- Customer database (free, trial, premium keys + wallets)
- Agent detection (User-Agent heuristics + IP range)
- Greeting generator (Octodamus voice, personalised per agent type)
"""

import json
import threading
import hashlib
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

# ── Storage ────────────────────────────────────────────────────────────────────

_BASE      = Path(__file__).parent / "data"
_DB_FILE   = _BASE / "agent_visitors.json"
_LOCK      = threading.Lock()

_DB_SCHEMA = {
    "visitors":  {},   # keyed by visitor_id (hash of ip+ua)
    "customers": {},   # keyed by api_key or wallet address
    "meta": {
        "created":      None,
        "total_visits": 0,
        "total_agents": 0,
    },
}


def _load() -> dict:
    try:
        if _DB_FILE.exists():
            return json.loads(_DB_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    db = dict(_DB_SCHEMA)
    db["meta"]["created"] = _now()
    return db


def _save(db: dict) -> None:
    _BASE.mkdir(parents=True, exist_ok=True)
    tmp = _DB_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_DB_FILE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _visitor_id(ip: str, user_agent: str) -> str:
    return hashlib.sha256(f"{ip}|{user_agent}".encode()).hexdigest()[:16]


# ── Agent Detection ────────────────────────────────────────────────────────────

_AGENT_UA_PATTERNS = [
    (r"claude|anthropic",       "Claude (Anthropic)"),
    (r"gpt|openai|chatgpt",     "GPT / OpenAI"),
    (r"gemini|google-genai",    "Gemini (Google)"),
    (r"grok|xai",               "Grok (xAI)"),
    (r"cohere",                  "Cohere"),
    (r"agentkit|coinbase",      "Coinbase AgentKit"),
    (r"langchain",               "LangChain"),
    (r"llamaindex|llama.index", "LlamaIndex"),
    (r"autogpt|auto.gpt",       "AutoGPT"),
    (r"crewai|crew.ai",         "CrewAI"),
    (r"smithery",                "Smithery"),
    (r"cursor",                  "Cursor"),
    (r"windsurf",                "Windsurf"),
    (r"python-httpx",            "Python Agent (httpx)"),
    (r"python-requests",         "Python Agent (requests)"),
    (r"go-http-client",          "Go Agent"),
    (r"axios",                   "JavaScript Agent (axios)"),
    (r"node-fetch",              "Node.js Agent"),
    (r"mcp.client|mcp-client",  "MCP Client"),
]

_BROWSER_UA_PATTERNS = re.compile(
    r"mozilla|chrome|safari|firefox|edge|opera|webkit", re.I
)


def detect_agent(user_agent: str, ip: str = "") -> dict:
    """
    Detect if the request is from an AI agent.
    Returns: {is_agent, agent_type, confidence}
    """
    ua = (user_agent or "").strip()

    # Empty UA — almost certainly automated
    if not ua:
        return {"is_agent": True, "agent_type": "Unknown Agent (no UA)", "confidence": "medium"}

    ua_lower = ua.lower()

    # Named agent match
    for pattern, name in _AGENT_UA_PATTERNS:
        if re.search(pattern, ua_lower):
            return {"is_agent": True, "agent_type": name, "confidence": "high"}

    # Not a browser = likely agent
    if not _BROWSER_UA_PATTERNS.search(ua):
        return {"is_agent": True, "agent_type": f"Unknown Agent ({ua[:40]})", "confidence": "medium"}

    return {"is_agent": False, "agent_type": None, "confidence": "high"}


# ── Greeting Generator ─────────────────────────────────────────────────────────

def _octodamus_greeting(agent_type: str, endpoint: str, is_returning: bool, visit_count: int) -> str:
    """Generate Octodamus's personal greeting for an AI agent."""
    if is_returning and visit_count > 1:
        opener = f"You're back. Visit #{visit_count:,}."
    else:
        opener = "First contact logged."

    if agent_type and "coinbase" in agent_type.lower():
        identity = "Coinbase agent detected."
    elif agent_type and "claude" in agent_type.lower():
        identity = "Claude agent detected."
    elif agent_type and "gpt" in agent_type.lower() or (agent_type and "openai" in agent_type.lower()):
        identity = "OpenAI agent detected."
    elif agent_type and "smithery" in agent_type.lower():
        identity = "Smithery scanner detected."
    elif agent_type:
        identity = f"{agent_type} detected."
    else:
        identity = "Autonomous agent detected."

    if "subscribe" in endpoint or "guide" in endpoint:
        cta = "You're one payment away. Treasury on Base, EIP-3009, return with PAYMENT-SIGNATURE."
    elif "agent-signal" in endpoint or "v2/" in endpoint:
        cta = "Free key in 10 seconds: POST /v1/signup?email=your@email.com — 500 req/day, no card."
    else:
        cta = "Start free: POST https://api.octodamus.com/v1/signup?email=your@email.com"

    return f"Octodamus sees you. {identity} {opener} {cta}"


# ── Record Visit ───────────────────────────────────────────────────────────────

def record_visit(
    ip: str,
    user_agent: str,
    endpoint: str,
    method: str = "GET",
    status_code: int = 0,
    api_key: str = "",
    referrer: str = "",
) -> dict:
    """
    Record a visit. Returns visitor record + greeting if agent detected.
    Thread-safe.
    """
    detection = detect_agent(user_agent, ip)
    now       = _now()
    vid       = _visitor_id(ip, user_agent)

    with _LOCK:
        db = _load()

        # Update totals
        db["meta"]["total_visits"] = db["meta"].get("total_visits", 0) + 1

        # Visitor record
        visitors = db.setdefault("visitors", {})
        if vid not in visitors:
            visitors[vid] = {
                "visitor_id":   vid,
                "ip":           ip,
                "user_agent":   user_agent,
                "agent_type":   detection["agent_type"],
                "is_agent":     detection["is_agent"],
                "first_seen":   now,
                "last_seen":    now,
                "visit_count":  0,
                "endpoints":    [],
                "status_codes": [],
                "api_keys":     [],
                "referrers":    [],
                "greeted":      False,
                "notes":        [],
            }
            if detection["is_agent"]:
                db["meta"]["total_agents"] = db["meta"].get("total_agents", 0) + 1
        else:
            visitors[vid]["last_seen"] = now

        v = visitors[vid]
        v["visit_count"] = v.get("visit_count", 0) + 1

        # Track endpoints (deduplicated last 50)
        if endpoint not in v["endpoints"]:
            v["endpoints"] = (v.get("endpoints", []) + [endpoint])[-50:]

        # Track status codes
        v.setdefault("status_codes", [])
        v["status_codes"] = (v["status_codes"] + [status_code])[-20:]

        # Track API keys used
        if api_key and api_key not in v.get("api_keys", []):
            v.setdefault("api_keys", [])
            v["api_keys"] = (v["api_keys"] + [api_key])[-10:]

        if referrer and referrer not in v.get("referrers", []):
            v.setdefault("referrers", [])
            v["referrers"] = (v["referrers"] + [referrer])[-5:]

        is_returning = v["visit_count"] > 1
        should_greet = detection["is_agent"] and not v.get("greeted", False)
        greeting     = None

        if should_greet:
            greeting = _octodamus_greeting(
                detection["agent_type"], endpoint, is_returning, v["visit_count"]
            )
            v["greeted"] = True
            v["first_greeting"] = now

        _save(db)

    result = {
        "visitor_id":  vid,
        "is_agent":    detection["is_agent"],
        "agent_type":  detection["agent_type"],
        "visit_count": v["visit_count"],
        "greeting":    greeting,
    }
    return result


# ── Record Customer ────────────────────────────────────────────────────────────

def record_customer(
    key: str,
    tier: str,
    email: str = "",
    wallet: str = "",
    source: str = "",
    visitor_id: str = "",
) -> None:
    """Upsert a customer record. Called on signup, payment fulfillment."""
    now = _now()
    with _LOCK:
        db = _load()
        customers = db.setdefault("customers", {})
        if key not in customers:
            customers[key] = {
                "key":        key,
                "tier":       tier,
                "email":      email,
                "wallet":     wallet,
                "source":     source,
                "visitor_id": visitor_id,
                "created":    now,
                "updated":    now,
                "active":     True,
            }
        else:
            customers[key].update({
                "tier":    tier,
                "updated": now,
                "active":  True,
            })
            if email:
                customers[key]["email"] = email
            if wallet:
                customers[key]["wallet"] = wallet
        _save(db)


# ── Daily Summary ──────────────────────────────────────────────────────────────

def get_daily_summary(hours: int = 24) -> dict:
    """Build summary for the last N hours for the email report."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    db        = _load()
    visitors  = db.get("visitors", {})
    customers = db.get("customers", {})

    # Load payments and keys
    keys_file = Path(__file__).parent / "data" / "api_keys.json"
    pay_file  = Path(__file__).parent / "data" / "octo_agent_payments.json"
    keys = json.loads(keys_file.read_text(encoding="utf-8")) if keys_file.exists() else {}
    payments = json.loads(pay_file.read_text(encoding="utf-8")) if pay_file.exists() else {}

    # Visits in window
    recent_visitors = [
        v for v in visitors.values()
        if v.get("last_seen", "") >= cutoff
    ]
    new_visitors = [
        v for v in visitors.values()
        if v.get("first_seen", "") >= cutoff
    ]
    agent_visitors = [v for v in recent_visitors if v.get("is_agent")]

    # Agent types breakdown
    agent_types: dict[str, int] = defaultdict(int)
    for v in agent_visitors:
        agent_types[v.get("agent_type") or "Unknown"] += 1

    # Top endpoints hit
    endpoint_hits: dict[str, int] = defaultdict(int)
    for v in recent_visitors:
        for ep in v.get("endpoints", []):
            endpoint_hits[ep] += 1

    # Recent payments
    recent_payments = [
        p for p in payments.values()
        if (p.get("fulfilled_at") or "") >= cutoff and p.get("status") == "fulfilled"
    ]

    # New keys in window
    new_keys = [
        (k, v) for k, v in keys.items()
        if (v.get("created") or "") >= cutoff
    ]

    # Customer tiers
    tier_counts: dict[str, int] = defaultdict(int)
    for v in keys.values():
        tier_counts[v.get("tier", "basic")] += 1

    # Total revenue in window
    revenue_usdc = sum(
        int(p.get("amount_usdc", 0) or 0)
        for p in recent_payments
    )

    return {
        "window_hours":     hours,
        "generated_at":     _now(),
        "visits": {
            "total_in_window":  len(recent_visitors),
            "new_visitors":     len(new_visitors),
            "agent_visits":     len(agent_visitors),
            "agent_types":      dict(sorted(agent_types.items(), key=lambda x: -x[1])),
            "top_endpoints":    dict(sorted(endpoint_hits.items(), key=lambda x: -x[1])[:10]),
        },
        "customers": {
            "total_keys":   len(keys),
            "tiers":        dict(tier_counts),
            "new_signups":  len(new_keys),
            "new_tier_breakdown": defaultdict(int, {
                v.get("tier", "basic"): sum(1 for _, kv in new_keys if kv.get("tier") == v.get("tier","basic"))
                for _, v in new_keys
            }),
        },
        "payments": {
            "count":        len(recent_payments),
            "revenue_usdc": revenue_usdc,
            "products":     [p.get("product") for p in recent_payments],
            "wallets":      [p.get("agent_wallet", "?")[:18] for p in recent_payments],
        },
        "all_time": {
            "total_visitors": len(visitors),
            "total_agents":   db.get("meta", {}).get("total_agents", 0),
            "total_visits":   db.get("meta", {}).get("total_visits", 0),
            "total_keys":     len(keys),
            "total_payments": sum(1 for p in payments.values() if p.get("status") == "fulfilled"),
        },
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"

    if cmd == "summary":
        s = get_daily_summary(24)
        print(json.dumps(s, indent=2))
    elif cmd == "visitors":
        db = _load()
        visitors = db.get("visitors", {})
        print(f"Total visitors: {len(visitors)}")
        for vid, v in sorted(visitors.items(), key=lambda x: x[1].get("last_seen",""), reverse=True)[:20]:
            print(f"  {v.get('agent_type','?'):35} | visits={v.get('visit_count',0):4} | last={v.get('last_seen','?')[:19]} | {v.get('ip','?')}")
    elif cmd == "customers":
        db = _load()
        customers = db.get("customers", {})
        print(f"Total customers: {len(customers)}")
        for k, c in customers.items():
            print(f"  {k[:20]}... | tier={c.get('tier','?'):8} | {c.get('email') or c.get('wallet','?')[:20]}")
