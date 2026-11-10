"""
tools/model_ranker.py
---------------------
Dynamically updates model tiers (high / medium / low) from the live OpenRouter
catalog, for AGENT use (text in -> text out, tool calling). No LLM is asked to
judge model quality; price is the main capability proxy, refined by recency,
context size and reasoning support.

On launch the script asks the user for a MAXIMUM PRICE (USD per 1M tokens).
No model in any tier will have an input OR output price above that number.
Press Enter to set no limit.

Pipeline
  1. Candidate filtering: text-only output (no image/audio/embedding models),
     tool calling required, valid non-negative pricing, not expired, not a
     router / ':' variant / specialty model, and within the user's PRICE CAP.
  2. Version de-duplication (gpt-x-2024-08-06 vs gpt-x-2024-11-20 -> newest).
  3. Tier bands are PERCENTILES of the cost distribution of what remains, so
     they adapt to the price cap and to catalog drift.
  4. Inside each band, models are ranked by a composite score.
  5. Provider diversity cap per tier (relaxed only when needed).
  6. Every tier gets up to `per_tier` models (default 5) and at least
     `min_per_tier` (default 4). If the percentile bands are too thin the
     catalog is split into three equal cost-rank slices instead. If the cap
     is so low that even that is impossible, the user is told the lowest cap
     that works and asked for a new number.
  7. Atomic write; a failed run never overwrites a good tiers file.

Non-interactive use (cron, CI): pass --max-price 15 or set MODEL_MAX_PRICE=15;
with no value and no terminal the script runs without a cap.

Output format of model_tiers.json is unchanged:
    {"high": [...ids], "medium": [...ids], "low": [...ids]}
"""
import argparse
import bisect
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from tools.llm_selector import fetch_openrouter_models

log = logging.getLogger("model_ranker")


def _env_float(name: str) -> Optional[float]:
    try:
        v = os.environ.get(name)
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
TIERS_FILE = os.environ.get("MODEL_TIERS_FILE", "model_tiers.json")

# Optional non-interactive default (USD per 1M tokens; None = ask / no cap).
MAX_PRICE: Optional[float] = _env_float("MODEL_MAX_PRICE")

MODELS_PER_TIER = 5        # target size of every tier
MIN_MODELS_PER_TIER = 4    # hard minimum

MIN_CONTEXT = 32_000       # agents need room for tool schemas + history
REFERENCE_CONTEXT = 1_000_000
MAX_AGE_DAYS = 540
MIN_POOL_AFTER_AGE_FILTER = 20     # else the age filter is skipped
REQUIRE_TOOLS = True
MAX_PER_PROVIDER = 2               # per tier; relaxed if a tier cannot be filled

# Ranking cost: typical agent workloads send more input than they receive.
INPUT_WEIGHT = 3.0
OUTPUT_WEIGHT = 1.0

# Tier bands: percentile of blended cost among eligible paid models.
# Bottom 10% = tiny/obscure models, top 3% = outlier pricing.
BANDS: Dict[str, Tuple[float, float]] = {
    "low":    (0.10, 0.50),
    "medium": (0.50, 0.80),
    "high":   (0.80, 0.97),
}
TIER_ORDER = ("high", "medium", "low")

# Composite score weights (inside a band).
W_PRICE = 0.50
W_RECENCY = 0.30
W_CONTEXT = 0.12
W_REASONING = 0.08

# Not general-purpose agent models.
EXCLUDE_ID_PATTERN = re.compile(
    r"embed|moderat|guard|rerank|tts|whisper|dall-e|stable-diffusion|"
    r"deep-research|-audio|audio-|image|-vision-only|roleplay|-rp\b",
    re.IGNORECASE,
)

_FAMILY_SUFFIXES = [
    re.compile(r"-\d{4}-\d{2}-\d{2}$"),                            # -2024-08-06
    re.compile(r"-\d{8}$"),                                        # -20240806
    re.compile(r"-(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$"),        # -0613 (MMDD)
    re.compile(r"-latest$"),
]


@dataclass
class Candidate:
    id: str
    cost: float                # blended USD per 1M tokens (used for ranking)
    peak_price: float          # max(input, output) USD per 1M (used for the cap)
    context: int
    created: Optional[int]     # unix seconds, may be unknown
    reasoning: bool
    cost_pct: float = 0.0      # percentile rank among eligible models (0..1)
    score: float = 0.0

    @property
    def provider(self) -> str:
        return self.id.split("/", 1)[0]


# --------------------------------------------------------------------------- #
# Parsing / filtering
# --------------------------------------------------------------------------- #
def _price_per_million(value: Any) -> Optional[float]:
    """USD/token -> USD/1M tokens. None for missing, malformed, NaN or negative
    values (OpenRouter uses -1 as a 'dynamic pricing' sentinel)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or v < 0:
        return None
    return v * 1_000_000


def _is_text_agent_model(m: Dict[str, Any]) -> bool:
    """Text must be an input, and TEXT must be the ONLY output modality
    (rejects image-generation, audio-output and embedding models)."""
    arch = m.get("architecture") or {}
    outs = arch.get("output_modalities")
    ins = arch.get("input_modalities")
    if outs is None and isinstance(arch.get("modality"), str) and "->" in arch["modality"]:
        left, right = arch["modality"].split("->", 1)
        outs = [x.strip() for x in right.split("+")]
        ins = ins or [x.strip() for x in left.split("+")]
    if outs is not None and set(outs) != {"text"}:
        return False
    if ins is not None and "text" not in ins:
        return False
    return True


def _is_expired(m: Dict[str, Any], today: date) -> bool:
    exp = m.get("expiration_date")
    if not exp:
        return False
    try:
        exp_date = date.fromisoformat(str(exp)[:10])
    except ValueError:
        return False
    return exp_date <= today + timedelta(days=14)


def _parse_candidate(
    m: Dict[str, Any],
    today: date,
    max_price: Optional[float],
) -> Optional[Candidate]:
    if not isinstance(m, dict):
        return None
    mid = m.get("id")
    if not isinstance(mid, str) or not mid:
        return None
    # Routers have dynamic pricing; ':' variants (:free, :nitro, :thinking ...)
    # are routing aliases of a base model, and :free ones are rate-limit traps.
    if mid.lower().startswith("openrouter/") or ":" in mid:
        return None
    if EXCLUDE_ID_PATTERN.search(mid):
        return None

    try:
        ctx = int(m.get("context_length") or 0)
    except (TypeError, ValueError):
        return None
    if ctx < MIN_CONTEXT:
        return None

    if not _is_text_agent_model(m) or _is_expired(m, today):
        return None

    pricing = m.get("pricing") or {}
    p_in = _price_per_million(pricing.get("prompt"))
    p_out = _price_per_million(pricing.get("completion"))
    if p_in is None or p_out is None:        # unknown price != free
        return None
    cost = (INPUT_WEIGHT * p_in + OUTPUT_WEIGHT * p_out) / (INPUT_WEIGHT + OUTPUT_WEIGHT)
    if cost <= 0:                            # free models skipped on purpose
        return None
    peak = max(p_in, p_out)

    # ---- user's price cap: neither input nor output may exceed it ----
    if max_price is not None and peak > max_price:
        return None

    params = m.get("supported_parameters")
    if REQUIRE_TOOLS and isinstance(params, list) and "tools" not in params:
        return None
    reasoning = isinstance(params, list) and (
        "reasoning" in params or "include_reasoning" in params
    )

    created = m.get("created")
    created = int(created) if isinstance(created, (int, float)) and created > 0 else None

    return Candidate(id=mid, cost=cost, peak_price=peak, context=ctx,
                     created=created, reasoning=reasoning)


def _family_key(model_id: str) -> str:
    key = model_id.lower()
    changed = True
    while changed:
        changed = False
        for pat in _FAMILY_SUFFIXES:
            new = pat.sub("", key)
            if new != key:
                key, changed = new, True
    return key


def _dedupe_versions(cands: List[Candidate]) -> List[Candidate]:
    """Keep only the newest snapshot per family (ties -> cheaper)."""
    best: Dict[str, Candidate] = {}
    for c in cands:
        k = _family_key(c.id)
        cur = best.get(k)
        if cur is None or (c.created or 0, -c.cost) > (cur.created or 0, -cur.cost):
            best[k] = c
    return list(best.values())


def _drop_stale(cands: List[Candidate], now_ts: float) -> List[Candidate]:
    cutoff = now_ts - MAX_AGE_DAYS * 86400
    fresh = [c for c in cands if c.created is None or c.created >= cutoff]
    return fresh if len(fresh) >= MIN_POOL_AFTER_AGE_FILTER else cands


def _eligible(raw_models: List[Dict[str, Any]], max_price: Optional[float],
              now_ts: float) -> List[Candidate]:
    today = date.fromtimestamp(now_ts)
    parsed = [c for c in (_parse_candidate(m, today, max_price) for m in raw_models) if c]
    return _drop_stale(_dedupe_versions(parsed), now_ts)


def min_cap_needed(raw_models: List[Dict[str, Any]], min_per_tier: int = MIN_MODELS_PER_TIER,
                   now_ts: Optional[float] = None) -> Optional[float]:
    """Lowest price cap (USD per 1M) that still leaves enough models for
    `min_per_tier` in every tier, or None if the catalog itself is too small."""
    now_ts = now_ts or time.time()
    need = min_per_tier * len(TIER_ORDER)
    peaks = sorted(c.peak_price for c in _eligible(raw_models, None, now_ts))
    return peaks[need - 1] if len(peaks) >= need else None


# --------------------------------------------------------------------------- #
# Scoring / selection
# --------------------------------------------------------------------------- #
def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _assign_percentiles(cands: List[Candidate]) -> None:
    costs = sorted(c.cost for c in cands)
    n = len(costs)
    for c in cands:
        c.cost_pct = bisect.bisect_left(costs, c.cost) / (n - 1) if n > 1 else 0.5


def _score(c: Candidate, band: Tuple[float, float], now_ts: float) -> float:
    lo, hi = band
    price_pos = _clamp((c.cost_pct - lo) / (hi - lo)) if hi > lo else 0.5
    if c.created is None:
        recency = 0.4
    else:
        age_days = max(0.0, (now_ts - c.created) / 86400)
        recency = _clamp(1 - age_days / MAX_AGE_DAYS)
    ctx = _clamp(
        math.log2(max(c.context, MIN_CONTEXT) / MIN_CONTEXT)
        / math.log2(REFERENCE_CONTEXT / MIN_CONTEXT)
    )
    return (
        W_PRICE * price_pos
        + W_RECENCY * recency
        + W_CONTEXT * ctx
        + W_REASONING * (1.0 if c.reasoning else 0.0)
    )


def _pick(ranked: List[Candidate], n: int) -> List[Candidate]:
    """Top-n with a per-provider cap; the cap is relaxed only to fill the tier."""
    chosen: List[Candidate] = []
    per_provider: Dict[str, int] = {}
    for c in ranked:
        if len(chosen) >= n:
            break
        if per_provider.get(c.provider, 0) < MAX_PER_PROVIDER:
            chosen.append(c)
            per_provider[c.provider] = per_provider.get(c.provider, 0) + 1
    for c in ranked:
        if len(chosen) >= n:
            break
        if c not in chosen:
            chosen.append(c)
    return chosen


def build_tiers(
    raw_models: List[Dict[str, Any]],
    max_price: Optional[float] = None,
    per_tier: int = MODELS_PER_TIER,
    min_per_tier: int = MIN_MODELS_PER_TIER,
    now_ts: Optional[float] = None,
) -> Optional[Dict[str, List[str]]]:
    """Pure function: catalog -> tiers dict, or None if the constraints
    (price cap + minimum models per tier) cannot be satisfied."""
    now_ts = now_ts or time.time()
    per_tier = max(per_tier, min_per_tier)

    cands = _eligible(raw_models, max_price, now_ts)
    log.info("%d raw -> %d eligible models (max price: %s)",
             len(raw_models), len(cands),
             f"${max_price:g}/1M" if max_price is not None else "none")

    need = min_per_tier * len(TIER_ORDER)
    if len(cands) < need:
        log.error("Only %d eligible models but %d are needed (%d tiers x %d).",
                  len(cands), need, len(TIER_ORDER), min_per_tier)
        return None

    _assign_percentiles(cands)

    # Mode A: percentile bands (trims tiny models and pricing outliers).
    groups = {
        name: [c for c in cands if BANDS[name][0] <= c.cost_pct < BANDS[name][1]]
        for name in TIER_ORDER
    }
    bands = dict(BANDS)

    # Mode B: when any band is too thin for the minimum (small pool or tight
    # price cap), split ALL eligible models into three contiguous cost slices.
    # Tiers stay strictly ordered by price and no model is shared between tiers.
    if any(len(g) < min_per_tier for g in groups.values()):
        log.info("Percentile bands too thin; using equal cost-rank slices instead.")
        ordered = sorted(cands, key=lambda c: c.cost)
        n = len(ordered)
        cut1, cut2 = round(n / 3), round(2 * n / 3)
        groups = {"low": ordered[:cut1], "medium": ordered[cut1:cut2], "high": ordered[cut2:]}
        bands = {t: (g[0].cost_pct, max(g[-1].cost_pct, g[0].cost_pct + 1e-9))
                 for t, g in groups.items() if g}

    chosen: Dict[str, List[Candidate]] = {}
    for name in TIER_ORDER:
        pool = groups[name]
        for c in pool:
            c.score = _score(c, bands[name], now_ts)
        chosen[name] = _pick(sorted(pool, key=lambda c: c.score, reverse=True), per_tier)

    short = {t: len(chosen[t]) for t in TIER_ORDER if len(chosen[t]) < min_per_tier}
    if short:
        log.error("Tiers below the minimum of %d models: %s", min_per_tier, short)
        return None

    for t in TIER_ORDER:
        log.info("%-6s %s", t, ", ".join(
            f"{c.id} (${c.cost:.2f} blended, max ${c.peak_price:.2f})" for c in chosen[t]))
    return {t: [c.id for c in chosen[t]] for t in TIER_ORDER}


# --------------------------------------------------------------------------- #
# User interaction
# --------------------------------------------------------------------------- #
QUIT = object()   # sentinel: the user chose to quit


def prompt_max_price(ask: Callable[[str], str] = input):
    """Ask for the maximum price until a valid answer is given.
    Returns a positive float, None (= no limit), or QUIT."""
    while True:
        try:
            raw = ask(
                "\nMaximum price for ANY model, in USD per 1M tokens "
                "(applies to input and output).\n"
                "Enter a number (e.g. 15), press Enter for no limit, or 'q' to quit: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return QUIT
        if not raw:
            return None
        if raw.lower() in ("q", "quit", "exit"):
            return QUIT
        try:
            value = float(raw.replace("$", "").replace(",", ".").strip())
        except ValueError:
            print(f"  '{raw}' is not a number. Try again.")
            continue
        if not math.isfinite(value) or value <= 0:
            print("  The price must be a positive number. Try again.")
            continue
        return value


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def _fetch_catalog() -> List[Dict[str, Any]]:
    try:
        try:
            raw = fetch_openrouter_models(force_refresh=True)   # bypass any cache
        except TypeError:
            raw = fetch_openrouter_models()                     # older signature
    except Exception as exc:
        log.error("Fetch failed: %s", exc)
        return []
    if isinstance(raw, dict):
        raw = raw.get("data", [])
    return raw if isinstance(raw, list) else []


def _atomic_write_json(path: str, data: Any) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def auto_update_tiers(
    max_price: Optional[float] = None,
    per_tier: int = MODELS_PER_TIER,
    min_per_tier: int = MIN_MODELS_PER_TIER,
    output_file: Optional[str] = None,
    interactive: bool = False,
    ask: Callable[[str], str] = input,
) -> bool:
    """Fetch the catalog and rewrite the tiers file.

    max_price    USD per 1M tokens; no model's input OR output price may exceed
                 it. Defaults to the MODEL_MAX_PRICE environment variable.
    interactive  if True and no max_price is known, ask the user for it, and
                 ask again (with a suggestion) if the cap leaves too few models.
    """
    path = output_file or TIERS_FILE
    if max_price is None:
        max_price = MAX_PRICE
    if max_price is None and interactive:
        max_price = prompt_max_price(ask)
        if max_price is QUIT:
            return False

    log.info("Fetching latest models from OpenRouter...")
    raw_models = _fetch_catalog()
    if not raw_models:
        log.error("Failed to fetch models from OpenRouter; keeping existing tiers.")
        return False

    while True:
        tiers = build_tiers(raw_models, max_price, per_tier, min_per_tier)
        if tiers:
            break
        if not interactive or max_price is None:
            log.error("Could not build valid tiers; keeping existing tiers.")
            return False
        hint = min_cap_needed(raw_models, min_per_tier)
        if hint is not None:
            print(f"\nA limit of ${max_price:g} leaves too few models "
                  f"(need {min_per_tier} per tier). A limit of about "
                  f"${math.ceil(hint * 100) / 100:g} or higher is enough.")
        else:
            print("\nThe catalog has too few usable models for this setup.")
        max_price = prompt_max_price(ask)
        if max_price is QUIT:
            return False

    try:
        with open(path, "r", encoding="utf-8") as f:
            if json.load(f) == tiers:
                log.info("Tiers unchanged (%s).", path)
                print(json.dumps(tiers, indent=2))
                return True
    except (OSError, ValueError):
        pass

    _atomic_write_json(path, tiers)
    log.info("Successfully updated %s", path)
    print(json.dumps(tiers, indent=2))
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Update model tiers from OpenRouter.")
    ap.add_argument("--max-price", type=float, default=None,
                    help="maximum price in USD per 1M tokens (skips the prompt)")
    ap.add_argument("--per-tier", type=int, default=MODELS_PER_TIER,
                    help="target models per tier (default 5)")
    ap.add_argument("--min-per-tier", type=int, default=MIN_MODELS_PER_TIER,
                    help="minimum models per tier, else abort (default 4)")
    ap.add_argument("--output", default=None, help="tiers file path")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="[Model Ranker] %(message)s")
    # Ask the user unless a cap was given by flag/env or there is no terminal.
    ok = auto_update_tiers(
        max_price=args.max_price,
        per_tier=args.per_tier,
        min_per_tier=args.min_per_tier,
        output_file=args.output,
        interactive=sys.stdin.isatty(),
    )
    raise SystemExit(0 if ok else 1)