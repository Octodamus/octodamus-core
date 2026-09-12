"""
octo_grok_kill.py -- owner kill switch for every xAI / Grok call.

Set 2026-09-12 after xAI billed over $100 in a single day. Grok's live x_search
is priced per source fetched, so a "cheap" sentiment call that pulls 20-30
posts costs real money, and the warm-cache task alone ran it ~120 times a day.

While GROK_DISABLED is True:
  - octo_grok_sentiment.get_grok_sentiment returns the _neutral() placeholder
    with live=False BEFORE touching its cache, so is_grok_live() is False and
    the paid surfaces (/v2/grok/brief, ACP Grok Sentiment Brief) refuse service
    without charging the buyer.
  - octo_grok_live and octo_grok_brief get no client (same as a missing key).
  - octodamus_runner never builds an x.ai client.
  - octo_grok_warm exits immediately.

To turn Grok back on: set GROK_DISABLED = False here, then re-enable the
Octodamus-GrokWarm task (Enable-ScheduledTask -TaskName Octodamus-GrokWarm).
For a one-off test without flipping the switch: OCTO_GROK_ENABLED=1 in the env.
"""
import os

GROK_DISABLED = True
DISABLED_REASON = "Grok disabled by owner 2026-09-12 (xAI billed >$100/day)"


def grok_disabled() -> bool:
    if os.environ.get("OCTO_GROK_ENABLED") == "1":
        return False
    return GROK_DISABLED
