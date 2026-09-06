"""
octo_oracle_registry.py -- Octodamus On-Chain Prediction Registry
Registers oracle call hashes and outcomes on Base mainnet (chain 8453).

Each oracle call gets two on-chain transactions:
  1. registerPrediction()  -- block.timestamp proves WHEN the call was made
  2. resolvePrediction()   -- records WIN/LOSS + exit price on resolution

Tamper-proof: block timestamp is immutable. Anyone can recompute the
content hash from the JSON record and verify it matches the on-chain entry.

Deploy:    python octo_oracle_registry.py deploy
Backfill:  python octo_oracle_registry.py backfill [--dry]
Status:    python octo_oracle_registry.py status
Verify:    python octo_oracle_registry.py verify <call_id>
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT         = Path(__file__).parent
SECRETS_FILE = ROOT / ".octo_secrets"
CONFIG_FILE  = ROOT / "data" / "onchain_config.json"
CALLS_FILE   = ROOT / "data" / "octo_calls.json"
CONTRACT_SOL = ROOT / "contracts" / "OctodamusOracle.sol"

BASE_RPC   = "https://mainnet.base.org"
BASE_CHAIN = 8453
BASESCAN   = "https://basescan.org"
PRICE_SCALE = 1000  # $74403.00 stored as 74403000


def _secrets() -> dict:
    try:
        raw = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
        return raw.get("secrets", raw)
    except Exception:
        return {}


def _load_config() -> dict:
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_config(cfg: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _load_calls() -> list:
    try:
        if CALLS_FILE.exists():
            return json.loads(CALLS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_calls(calls: list):
    CALLS_FILE.write_text(json.dumps(calls, indent=2), encoding="utf-8")


def _price_to_uint(price) -> int:
    if price is None:
        return 0
    return int(round(float(price) * PRICE_SCALE))


def _made_at_to_unix(made_at: str) -> int:
    try:
        dt = datetime.strptime(made_at.replace(" UTC", ""), "%Y-%m-%d %H:%M")
        return int(dt.replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        return 0


_HASH_VERSION_CURRENT = 2


def _make_content_hash(call: dict) -> bytes:
    """
    v1: keccak256(callId, asset, direction, entryPrice, madeAtUnix)
    v2: keccak256(callId, asset, direction, entryPrice, madeAtUnix, callType)

    Anyone can recompute this from the JSON to verify the call was committed
    before the outcome was known. v2 additionally commits the STRATEGY that
    produced the call, so a per-strategy win rate is provable rather than
    asserted -- previously call_type lived only in local JSON, which meant a
    third party could verify the blended record but not any breakdown of it.

    Version is read from the call, never assumed. Calls published before the
    v2 cutover carry no hash_version and MUST keep hashing as v1 -- otherwise
    every historical call fails verification against the chain.
    """
    from web3 import Web3
    types = ["uint32", "string", "string", "uint256", "uint256"]
    vals  = [
        int(call["id"]),
        call["asset"].upper(),
        call["direction"].upper(),
        _price_to_uint(call.get("entry_price", 0)),
        _made_at_to_unix(call.get("made_at", "")),
    ]
    if int(call.get("hash_version") or 1) < 2:
        return Web3.solidity_keccak(types, vals)
    return Web3.solidity_keccak(
        types + ["string"],
        vals  + [str(call.get("call_type") or "oracle")],
    )


def _get_web3():
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(BASE_RPC))
    if not w3.is_connected():
        raise RuntimeError(f"Cannot connect to Base RPC: {BASE_RPC}")
    return w3


def _get_account(w3):
    from eth_account import Account
    pk = _secrets().get("FRANKLIN_PRIVATE_KEY", "")
    if not pk:
        raise RuntimeError("FRANKLIN_PRIVATE_KEY not found in .octo_secrets")
    return Account.from_key(pk)


def _get_contract(w3):
    cfg  = _load_config()
    addr = cfg.get("contract_address")
    abi  = cfg.get("abi")
    if not addr or not abi:
        raise RuntimeError("Contract not deployed. Run: python octo_oracle_registry.py deploy")
    return w3.eth.contract(address=addr, abi=abi)


def _send_tx(w3, account, fn_call) -> str:
    nonce     = w3.eth.get_transaction_count(account.address)
    gas_est   = fn_call.estimate_gas({"from": account.address})
    gas_price = w3.eth.gas_price
    tx = fn_call.build_transaction({
        "from":     account.address,
        "nonce":    nonce,
        "gas":      int(gas_est * 1.2),
        "gasPrice": int(gas_price * 1.1),
        "chainId":  BASE_CHAIN,
    })
    signed  = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] != 1:
        raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")
    return tx_hash.hex()


# ── Deploy ─────────────────────────────────────────────────────────────────────

def deploy() -> str:
    """Compile and deploy OctodamusOracle.sol to Base mainnet. One-time operation."""
    try:
        import solcx
    except ImportError:
        print("[Registry] Installing py-solc-x...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "py-solc-x"])
        import solcx

    print("[Registry] Compiling OctodamusOracle.sol (solc 0.8.20)...")
    solcx.install_solc("0.8.20", show_progress=True)
    source   = CONTRACT_SOL.read_text(encoding="utf-8")
    compiled = solcx.compile_source(source, output_values=["abi", "bin"], solc_version="0.8.20")
    abi      = compiled["<stdin>:OctodamusOracle"]["abi"]
    bytecode = compiled["<stdin>:OctodamusOracle"]["bin"]

    w3      = _get_web3()
    account = _get_account(w3)
    bal_eth = w3.from_wei(w3.eth.get_balance(account.address), "ether")
    print(f"[Registry] Deployer: {account.address}")
    print(f"[Registry] ETH balance: {bal_eth:.6f} ETH")
    if float(bal_eth) < 0.001:
        print("[Registry] WARNING: Low ETH balance -- fund the wallet before deploying.")
        print("[Registry] You need ~0.001 ETH on Base for deployment gas.")
        return ""

    Contract  = w3.eth.contract(abi=abi, bytecode=bytecode)
    nonce     = w3.eth.get_transaction_count(account.address)
    gas_est   = Contract.constructor().estimate_gas({"from": account.address})
    gas_price = w3.eth.gas_price
    tx_data   = Contract.constructor().build_transaction({
        "from":     account.address,
        "nonce":    nonce,
        "gas":      int(gas_est * 1.2),
        "gasPrice": int(gas_price * 1.1),
        "chainId":  BASE_CHAIN,
    })
    signed  = account.sign_transaction(tx_data)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"[Registry] Deploy tx: {BASESCAN}/tx/{tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] != 1:
        raise RuntimeError("Deploy transaction failed.")

    address = receipt["contractAddress"]
    print(f"[Registry] Contract deployed: {BASESCAN}/address/{address}")

    _save_config({
        "contract_address": address,
        "deploy_tx":        tx_hash.hex(),
        "deployer":         account.address,
        "deployed_at":      datetime.now(timezone.utc).isoformat(),
        "chain_id":         BASE_CHAIN,
        "rpc":              BASE_RPC,
        "abi":              abi,
    })
    print(f"[Registry] Config saved: {CONFIG_FILE}")
    return address


# ── Publish prediction ─────────────────────────────────────────────────────────

def publish_prediction(call: dict) -> Optional[str]:
    """
    Register a prediction on-chain. Returns tx_hash or None on failure.
    Writes tx_hash into the call dict -- caller must persist to JSON.
    No-op if call already has tx_hash.
    """
    if call.get("tx_hash"):
        return None

    try:
        w3       = _get_web3()
        account  = _get_account(w3)
        contract = _get_contract(w3)

        # Stamp the hash version BEFORE hashing. The caller must persist this
        # alongside tx_hash or the call can never be verified again.
        call["hash_version"] = _HASH_VERSION_CURRENT

        fn = contract.functions.registerPrediction(
            int(call["id"]),
            _make_content_hash(call),
            call["asset"].upper(),
            call["direction"].upper(),
            _price_to_uint(call.get("entry_price", 0)),
            _price_to_uint(call.get("target_price") or 0),
            (call.get("timeframe") or "")[:32],
        )
        tx = _send_tx(w3, account, fn)
        call["tx_hash"] = tx
        print(f"[Registry] #{call['id']} registered: {BASESCAN}/tx/{tx}")
        return tx

    except Exception as e:
        print(f"[Registry] publish_prediction #{call.get('id')} failed: {e}")
        return None


# ── Resolve prediction ─────────────────────────────────────────────────────────

def resolve_prediction(call_id: int, won: bool, exit_price: float) -> Optional[str]:
    """
    Record WIN/LOSS outcome on-chain. Returns tx_hash or None on failure.
    """
    try:
        w3       = _get_web3()
        account  = _get_account(w3)
        contract = _get_contract(w3)

        fn  = contract.functions.resolvePrediction(
            int(call_id),
            bool(won),
            _price_to_uint(exit_price),
        )
        tx = _send_tx(w3, account, fn)
        print(f"[Registry] #{call_id} resolved ({'WIN' if won else 'LOSS'}): {BASESCAN}/tx/{tx}")
        return tx

    except Exception as e:
        print(f"[Registry] resolve_prediction #{call_id} failed: {e}")
        return None


# ── Backfill existing calls ────────────────────────────────────────────────────

def backfill_existing_calls(dry_run: bool = False):
    """
    Register and resolve all existing oracle calls that aren't yet on-chain.
    Run once after deploying to build the full historical record.
    """
    calls   = _load_calls()
    oracle  = [c for c in calls if c.get("call_type", "oracle") == "oracle"]
    pending = [c for c in oracle if not c.get("tx_hash")]

    print(f"[Registry] {len(oracle)} oracle calls total, {len(pending)} need publishing.")

    if dry_run:
        for c in pending:
            h = _make_content_hash(c)
            print(f"  DRY #{c['id']:03d} {c['asset']:5s} {c['direction']} {c.get('outcome','OPEN'):4s}  hash={h.hex()[:20]}...")
        return

    updated = 0
    for call in oracle:
        if call.get("tx_hash"):
            print(f"[Registry] #{call['id']} already on-chain.")
            continue

        tx = publish_prediction(call)
        if not tx:
            continue
        updated += 1

        if call.get("resolved") and call.get("outcome") in ("WIN", "LOSS"):
            won    = (call["outcome"] == "WIN")
            res_tx = resolve_prediction(call["id"], won, call.get("exit_price") or 0)
            if res_tx:
                call["resolve_tx_hash"] = res_tx

    if updated > 0:
        _save_calls(calls)
        print(f"[Registry] Done -- {updated} calls registered on Base mainnet.")
    else:
        print("[Registry] Nothing to backfill.")


# ── Verify ─────────────────────────────────────────────────────────────────────

def verify_call(call_id: int):
    """
    Verify the on-chain content hash matches the local JSON record.
    VERIFIED = prediction data was not altered after publishing.
    """
    calls = _load_calls()
    call  = next((c for c in calls if c["id"] == call_id), None)
    if not call:
        print(f"[Registry] Call #{call_id} not in local JSON.")
        return

    if not call.get("tx_hash"):
        print(f"[Registry] Call #{call_id} not yet published on-chain.")
        return

    try:
        w3       = _get_web3()
        contract = _get_contract(w3)
        pred     = contract.functions.getPrediction(int(call_id)).call()

        local_hash   = _make_content_hash(call)
        onchain_hash = bytes(pred[0])
        match        = (local_hash == onchain_hash)

        print(f"\nCall #{call_id} {call['asset']} {call['direction']} -- {'VERIFIED' if match else 'MISMATCH'}")
        print(f"  Local hash:    {local_hash.hex()}")
        print(f"  On-chain hash: {onchain_hash.hex()}")
        if pred[8]:  # resolved
            result = "WIN" if pred[9] else "LOSS"
            print(f"  Outcome: {result} | Exit: ${pred[10] / PRICE_SCALE:,.3f}")
        else:
            print(f"  Outcome: OPEN")
        print(f"  BaseScan: {BASESCAN}/tx/{call['tx_hash']}")
        print()

    except Exception as e:
        print(f"[Registry] Verify failed: {e}")


# ── Status ─────────────────────────────────────────────────────────────────────

def show_status():
    cfg = _load_config()
    if not cfg.get("contract_address"):
        print("[Registry] Not deployed. Run: python octo_oracle_registry.py deploy")
        return

    calls  = _load_calls()
    oracle = [c for c in calls if c.get("call_type", "oracle") == "oracle"]
    n_pub  = sum(1 for c in oracle if c.get("tx_hash"))
    n_res  = sum(1 for c in oracle if c.get("resolve_tx_hash"))

    print(f"""
Octodamus On-Chain Oracle Registry
====================================
Contract:  {BASESCAN}/address/{cfg['contract_address']}
Deployed:  {cfg.get('deployed_at','?')[:19]}
Deployer:  {cfg.get('deployer','?')}

Oracle calls:      {len(oracle)} total
Registered:        {n_pub} / {len(oracle)} on-chain
Outcomes recorded: {n_res} / {len([c for c in oracle if c.get('resolved')])} resolved
""")

    for c in oracle:
        tx  = c.get("tx_hash", "")
        rtx = c.get("resolve_tx_hash", "")
        pub = (tx[:10] + "...") if tx else "NOT PUBLISHED"
        res = (rtx[:10] + "...") if rtx else ("-" if not c.get("resolved") else "not recorded")
        print(f"  #{c['id']:03d} {c['asset']:6s} {c['direction']:4s}  {c.get('outcome','OPEN'):4s}  {pub}  {res}")


# ── BaseScan contract verification ─────────────────────────────────────────────

def verify_on_basescan() -> bool:
    """
    Submit OctodamusOracle.sol source to BaseScan for verification.
    Once verified, all transactions show decoded human-readable parameters
    (asset, direction, price, timestamp) instead of raw hex calldata.

    Requires BASESCAN_API_KEY in .octo_secrets (free at basescan.org/apis).
    """
    import time
    try:
        import requests
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
        import requests

    cfg = _load_config()
    address = cfg.get("contract_address")
    if not address:
        print("[Registry] Contract not deployed. Run: python octo_oracle_registry.py deploy")
        return False

    api_key = _secrets().get("BASESCAN_API_KEY") or _secrets().get("ETHERSCAN_API_KEY", "")
    if not api_key:
        print("[Registry] No BASESCAN_API_KEY or ETHERSCAN_API_KEY found in .octo_secrets")
        print("[Registry] Get a free key at: https://etherscan.io/myapikey (works on BaseScan too)")
        return False

    source = CONTRACT_SOL.read_text(encoding="utf-8")

    # Etherscan V2 unified API — chainid=8453 targets Base mainnet
    V2_URL = "https://api.etherscan.io/v2/api"

    print(f"[Registry] Submitting {address} to BaseScan for verification (Etherscan V2)...")
    resp = requests.post(
        V2_URL,
        params={"chainid": 8453},
        data={
            "module":           "contract",
            "action":           "verifysourcecode",
            "apikey":           api_key,
            "contractaddress":  address,
            "sourceCode":       source,
            "codeformat":       "solidity-single-file",
            "contractname":     "OctodamusOracle",
            "compilerversion":  "v0.8.20+commit.a1b79de6",
            "optimizationUsed": "0",
            "runs":             "200",
            "evmversion":       "paris",
            "licenseType":      "1",  # MIT
        },
        timeout=30,
    )
    data = resp.json()
    if data.get("status") != "1":
        print(f"[Registry] Submission failed: {data.get('result', data)}")
        return False

    guid = data["result"]
    print(f"[Registry] Verification submitted. GUID: {guid}")
    print("[Registry] Polling for result (may take 30-60s)...")

    for attempt in range(12):
        time.sleep(10)
        check = requests.get(
            V2_URL,
            params={
                "chainid": 8453,
                "module":  "contract",
                "action":  "checkverifystatus",
                "guid":    guid,
                "apikey":  api_key,
            },
            timeout=15,
        ).json()
        result = check.get("result", "")
        print(f"[Registry] Attempt {attempt+1}: {result}")
        if "Pass" in result or "Already Verified" in result:
            print(f"[Registry] Verified! {BASESCAN}/address/{address}#code")
            print(f"[Registry] All transactions now show decoded parameters on BaseScan.")
            return True
        if "Fail" in result or "Error" in result:
            print(f"[Registry] Verification failed: {result}")
            return False

    print("[Registry] Timed out. Check manually: " + f"{BASESCAN}/address/{address}#code")
    return False


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] == "status":
        show_status()
    elif args[0] == "deploy":
        deploy()
    elif args[0] == "backfill":
        backfill_existing_calls(dry_run="--dry" in args)
    elif args[0] == "verify":
        if len(args) < 2:
            print("Usage: python octo_oracle_registry.py verify <call_id>")
        else:
            verify_call(int(args[1]))
    elif args[0] == "verify_contract":
        verify_on_basescan()
    else:
        print("Usage:")
        print("  python octo_oracle_registry.py deploy")
        print("  python octo_oracle_registry.py backfill [--dry]")
        print("  python octo_oracle_registry.py status")
        print("  python octo_oracle_registry.py verify <call_id>")
        print("  python octo_oracle_registry.py verify_contract   -- submit source to BaseScan")
