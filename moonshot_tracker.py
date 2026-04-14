"""
moonshot_tracker.py — Octodamus 2026 Prediction Tracker

Source: Moonshots Podcast end-of-2025 predictions episode
Hosts: Peter Diamandis, Alex AWG, Dave DB2, Salim, Emad

Tracks 10 macro predictions. Called by octodamus_runner.py mode_moonshot.
"""

PREDICTIONS = [
    {
        "id": "space_race",
        "title": "2026 Space Race Is On",
        "source": "Peter Diamandis",
        "summary": (
            "Blue Origin lands at Shackleton Crater (lunar south pole) before SpaceX. "
            "SpaceX perfects orbital refueling in 2026 to prep Starship for Mars (launch window 2027). "
            "Three-way race: Blue Origin vs SpaceX vs China."
        ),
        "signals_to_watch": [
            "Blue Origin New Glenn launch schedule",
            "SpaceX Starship orbital refueling test",
            "NASA Artemis lunar south pole missions",
            "China lunar landing program updates",
            "Shackleton crater mission news",
        ],
        "status": "tracking",
        "confidence_given": "30%",
    },
    {
        "id": "millennium_math",
        "title": "AI Solves a Millennium Prize Problem",
        "source": "Alex AWG",
        "summary": (
            "One of the six remaining Clay Mathematics Institute Millennium Prize problems "
            "gets solved by AI. Most likely: Navier-Stokes (Google DeepMind team of 12 working on it) "
            "or Riemann Hypothesis (xAI interest). Math community will likely complain it's brute force."
        ),
        "signals_to_watch": [
            "Google DeepMind math research announcements",
            "xAI Riemann hypothesis progress",
            "Clay Mathematics Institute news",
            "AI theorem proving breakthroughs",
            "Navier-Stokes partial solutions",
            "FrontierMath benchmark scores",
        ],
        "status": "tracking",
        "confidence_given": None,
    },
    {
        "id": "100x_ai_models",
        "title": "100x Leap in AI Model Size / Capability",
        "source": "Dave DB2",
        "summary": (
            "Not 40x but 100x year because of underestimated quantization gains. "
            "FP4 and ternary weights from Chinese research (driven by chip embargo) shrink parameters "
            "dramatically while boosting inference speed. Speed = intelligence. "
            "Chinese open-source flows back to US. Models at end of 2026 are 100x bigger in effective parameter use."
        ),
        "signals_to_watch": [
            "FP4 quantization model releases",
            "Ternary weight AI model announcements",
            "DeepSeek and Chinese AI model releases",
            "Nvidia Blackwell chip deployment",
            "AI inference speed benchmarks",
            "New model parameter counts",
            "Chinese chip fab progress (Huawei, SMIC)",
        ],
        "status": "tracking",
        "confidence_given": None,
    },
    {
        "id": "ai_native_rewrites",
        "title": "Digital Transformation Dead — AI-Native Rewrites Begin",
        "source": "Salim",
        "summary": (
            "Companies stop patching legacy systems and build AI-native equivalents on the edge "
            "with 10-20x fewer employees. End of 'digital transformation' consulting era. "
            "AI-first workflows replace human-centric flows. "
            "Human role becomes exception handling and spot-checking."
        ),
        "signals_to_watch": [
            "Major enterprise AI transformation announcements",
            "Consulting firm revenue and layoffs",
            "AI workforce reduction case studies",
            "McKinsey Accenture Deloitte AI strategy pivots",
            "Fortune 500 headcount vs AI spend",
            "Agentic workflow enterprise deployments",
        ],
        "status": "tracking",
        "confidence_given": None,
    },
    {
        "id": "remote_turing_test",
        "title": "Remote Turing Test — Can't Tell AI from Human on Zoom",
        "source": "Emad",
        "summary": (
            "Full-stack AI employees (accountants, lawyers, marketers) indistinguishable from humans "
            "on 1080p/4K Zoom calls. Preference studies can't determine if coworker is AI or human. "
            "Companies will have AI teammates with personalities. "
            "State laws may require AI self-identification but federal law may override."
        ),
        "signals_to_watch": [
            "AI avatar video call products",
            "Real-time AI voice and video generation",
            "AI employee / digital worker platforms",
            "AI self-identification regulation news",
            "HeyGen Synthesia D-ID product updates",
            "AI latency in real-time conversation",
        ],
        "status": "tracking",
        "confidence_given": None,
    },
    {
        "id": "benchmark_saturation",
        "title": "AI Benchmark Saturation — 90% on Economic Tasks",
        "source": "Alex AWG",
        "summary": (
            "GDP-Val surpasses 90% (was 70.9% with GPT-5.2). "
            "Humanity's Last Exam hits 75% (was 45% with Gemini 3 Pro). "
            "FrontierMath Tier 4 hits 40% (was 19% with Gemini 3 Pro). "
            "90% GDP-Val = ~90% of knowledge work automatable. "
            "Benchmark saturation signals mass automation of knowledge work."
        ),
        "signals_to_watch": [
            "GDP-Val benchmark scores",
            "Humanity's Last Exam leaderboard",
            "FrontierMath benchmark results",
            "OpenAI GPT-5 release and evals",
            "Google Gemini 3 Ultra benchmarks",
            "Anthropic Claude 4 benchmark results",
            "Knowledge worker automation studies",
        ],
        "status": "tracking",
        "confidence_given": None,
    },
    {
        "id": "ai_billionaire",
        "title": "New Billionaires from Unknown Acronyms + First AI Billionaire",
        "source": "Dave DB2",
        "summary": (
            "A new 3-4 letter AI acronym emerges that barely exists now — produces multiple young billionaires. "
            "Like RLHF did for Mercor/Scale AI. "
            "Separately: first AI entity with construable net worth of $1B+ "
            "(likely in trading/crypto — Grok 4.2 already profitable in trading championships). "
            "Single-person billion-dollar startup imminent."
        ),
        "signals_to_watch": [
            "New AI company unicorn valuations",
            "Mercor Scale AI Surge growth",
            "AI trading fund performance",
            "Autonomous AI crypto trading",
            "Grok xAI trading results",
            "New AI infrastructure company funding rounds",
            "Solo founder startup valuations",
        ],
        "status": "tracking",
        "confidence_given": None,
    },
    {
        "id": "education_split",
        "title": "Education Splits — Credential Factories vs Agency Accelerators",
        "source": "Salim",
        "summary": (
            "Traditional credential-based education begins collapse. "
            "New model optimizes for AI fluency, resilience, agency. "
            "Portfolio of what you built replaces degrees. "
            "GitHub rating / build portfolio matters more than CS degree. "
            "College tuition may hit peak and start declining. "
            "Value shifts to demonstrated capability over credentials."
        ),
        "signals_to_watch": [
            "College enrollment and tuition trends",
            "University closures and mergers",
            "AI bootcamp and alternative education growth",
            "Big tech hiring credential requirements changes",
            "GitHub portfolio hiring trends",
            "New AI-native education platforms",
        ],
        "status": "tracking",
        "confidence_given": None,
    },
    {
        "id": "level5_autonomy",
        "title": "Level 5 Autonomy — Robots and Cars Achieve Full Generalized Autonomy",
        "source": "Emad",
        "summary": (
            "Self-driving cars currently at level 4, robots at level 2. "
            "With enough compute (Blackwell clusters), level 5 becomes technically achievable in 2026 — "
            "even if edge deployment lags. "
            "Humanoid robots close to $20K with $200K of cloud compute achieving full autonomy. "
            "Physical AI = biggest AGI step forward. "
            "Regulatory environment will call it 'enhanced level 4' to avoid scrutiny."
        ),
        "signals_to_watch": [
            "Waymo Tesla FSD expansion",
            "Figure 1X Optimus robot capabilities",
            "Humanoid robot dexterity demos",
            "Physical AI world model releases",
            "Robot pricing announcements",
            "Autonomous vehicle regulatory approvals",
            "Chinese humanoid robot manufacturers",
        ],
        "status": "tracking",
        "confidence_given": None,
    },
    {
        "id": "age_reversal",
        "title": "Kitty Hawk Moment for Age Reversal — Epigenetic Reprogramming in Humans",
        "source": "Peter Diamandis",
        "summary": (
            "Life Biosciences (David Sinclair) enters human trials Q1 2026. "
            "Partial epigenetic reprogramming using 3 Yamanaka factors (OSK — without cMYC). "
            "First target: NAION (eye stroke) and glaucoma. Then liver disease (MASH). "
            "Success = whole-body age reversal pathway. "
            "AAV delivery currently $500K-$1M per treatment. Pill version in development (~$200/month). "
            "Longevity escape velocity predicted 2030-2032."
        ),
        "signals_to_watch": [
            "Life Biosciences human trial results",
            "David Sinclair epigenetic reprogramming news",
            "Yamanaka factors clinical trials",
            "Longevity biotech funding rounds",
            "RetroBio Altos Labs Calico research",
            "XPRIZE Healthspan competition",
            "Longevity escape velocity predictions",
            "Partial reprogramming pill development",
        ],
        "status": "tracking",
        "confidence_given": None,
    },
]


def get_all_predictions() -> list[dict]:
    return PREDICTIONS


def get_prediction(pred_id: str) -> dict | None:
    return next((p for p in PREDICTIONS if p["id"] == pred_id), None)


def build_moonshot_context(max_predictions: int = 3) -> str:
    """
    Build a compact context string for Claude prompts.
    Rotates through predictions so each gets coverage over time.
    """
    import json
    from pathlib import Path
    from datetime import datetime

    # Track rotation state
    state_file = Path(__file__).parent / "data" / "moonshot_state.json"
    state_file.parent.mkdir(exist_ok=True)

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        state = {"last_index": 0}

    start = state.get("last_index", 0) % len(PREDICTIONS)
    selected = PREDICTIONS[start:start + max_predictions]
    if len(selected) < max_predictions:
        selected += PREDICTIONS[:max_predictions - len(selected)]

    # Advance rotation
    state["last_index"] = (start + max_predictions) % len(PREDICTIONS)
    state_file.write_text(json.dumps(state), encoding="utf-8")

    lines = ["=== 2026 MOONSHOT PREDICTIONS TRACKER ==="]
    lines.append("Source: Moonshots Podcast end-of-year predictions\n")
    for p in selected:
        lines.append(f"PREDICTION: {p['title']}")
        lines.append(f"  {p['summary']}")
        lines.append(f"  Watch for: {', '.join(p['signals_to_watch'][:3])}")
        lines.append("")
    lines.append("Your job: Find the most interesting signal on ONE of these predictions right now.")
    lines.append("What has actually happened? What's the status? What does the data say?")
    return "\n".join(lines)
