import os
import json
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Hardened secret / environment loading
# ---------------------------------------------------------------------------
# Only environment variable names matching this pattern may be set from a
# .env file. This blocks injection of arbitrary (or framework-critical)
# variables such as PYTHONPATH, LD_PRELOAD, or PATH via a malicious .env.
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

# Values that must never be overridden from a .env file.
_PROTECTED_ENV_NAMES = frozenset({
    "PATH", "PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "LD_LIBRARY_PATH",
    "SHELL", "HOME", "USER", "IFS", "PS1",
})

# Substrings that mark a value as a secret for redaction purposes.
_SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def _is_secret_name(name: str) -> bool:
    return any(hint in name.upper() for hint in _SECRET_HINTS)


def _redact(name: str, value: str) -> str:
    """Return a redacted representation of a secret value for logs/errors."""
    if not _is_secret_name(name) or not value:
        return "<unset>" if not value else value
    if len(value) <= 8:
        return "***"
    return value[:4] + "***" + value[-4:]


def redact_secret(name: str, value: str) -> str:
    """Public redaction helper: safe to call on any name/value pair."""
    return _redact(name, value)


def redact_mapping(mapping: dict) -> dict:
    """
    Return a copy of a mapping with all secret-looking values redacted.

    Useful for logging environment snapshots, request payloads, or debug
    dumps without leaking credentials.
    """
    if not isinstance(mapping, dict):
        return {}
    return {
        str(k): _redact(str(k), v) if isinstance(v, str) else v
        for k, v in mapping.items()
    }


def safe_log(message: str, **kwargs) -> str:
    """
    Format a log message with redacted keyword arguments.

    Any kwarg whose name looks secret-like (KEY/TOKEN/SECRET/PASSWORD/
    CREDENTIAL) is redacted before interpolation. Returns the formatted
    string; the caller decides how to emit it.
    """
    safe_kwargs = redact_mapping(kwargs)
    try:
        return message.format(**safe_kwargs)
    except (KeyError, IndexError, ValueError):
        return message


def _safe_load_env_file(env_path: str) -> dict:
    """
    Parse a .env file without eval/exec/shell expansion.

    Rules:
      - Only KEY=VALUE lines (and # comments) are accepted.
      - KEY must match _ENV_NAME_RE and must not be protected.
      - Values are taken literally; no interpolation, no backtick/$(...)
        expansion, no escape interpretation beyond stripping matching
        surrounding quotes.
      - Malformed lines are skipped (never raise, never log the value).
    """
    loaded = {}
    path = Path(env_path)
    if not path.is_file():
        return loaded
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return loaded

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip()
        if not _ENV_NAME_RE.match(name) or name in _PROTECTED_ENV_NAMES:
            continue
        # Reject lines attempting shell-style expansion or command injection.
        lowered = value.lower()
        if any(tok in lowered for tok in ("$(", "`", "eval", "exec", "import ")):
            continue
        # Strip one pair of matching surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not value:
            continue
        loaded[name] = value
        os.environ.setdefault(name, value)
    return loaded


# Load .env from the module directory only (never cwd-relative, which could
# pick up an attacker-controlled file from a writable directory).
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
_safe_load_env_file(_ENV_FILE)


def _get_secret(name: str) -> str:
    """
    Fetch a secret from the environment with strict validation.

    Returns an empty string when unset. Never raises with the raw value;
    errors reference the redacted form only.
    """
    value = os.environ.get(name, "")
    if not value:
        return ""
    if len(value) > 512:
        # Implausibly long secrets are rejected outright.
        raise ValueError(
            f"Environment variable {name} has an invalid length "
            f"(>512 chars); refusing to use it."
        )
    if any(ch in value for ch in ("\n", "\r", "\x00")):
        raise ValueError(
            f"Environment variable {name} contains control characters; "
            f"refusing to use it."
        )
    return value


OPENROUTER_API_KEY = _get_secret("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# ---------------------------------------------------------------------------
# Secret-bearing key registry and strict validation rules
# ---------------------------------------------------------------------------
# Each entry: kind of secret -> validation rule. Rules are applied by
# validate_secret() below. A secret that fails validation is never used.
_SECRET_KEY_RULES = {
    "openrouter_api_key": {
        "env_names": ("OPENROUTER_API_KEY",),
        "min_length": 20,
        "max_length": 512,
        # OpenRouter keys are `sk-or-v1-<hex/base62>`.
        "required_prefix": "sk-or-",
        "charset_re": re.compile(r"^[A-Za-z0-9_\-]+$"),
    },
}

# Flat registry: every env var name that holds a secret, mapped to its rule.
SECRET_BEARING_KEYS = {
    env_name: rule
    for rule in _SECRET_KEY_RULES.values()
    for env_name in rule["env_names"]
}


def validate_secret(name: str, value: str) -> str:
    """
    Strictly validate a secret-bearing env var against its registered rule.

    Returns the value when it conforms. Raises ValueError (message never
    contains the raw value) when the value is malformed, in which case the
    caller must treat the secret as unset/refuse to use it.
    """
    rule = SECRET_BEARING_KEYS.get(name)
    if rule is None:
        # Unknown names have no strict rule; fall back to basic sanity only.
        return value
    if not value:
        return value
    if not (rule["min_length"] <= len(value) <= rule["max_length"]):
        raise ValueError(
            f"Secret '{name}' failed validation: invalid length "
            f"(expected {rule['min_length']}..{rule['max_length']})."
        )
    prefix = rule.get("required_prefix")
    if prefix and not value.startswith(prefix):
        raise ValueError(
            f"Secret '{name}' failed validation: unexpected shape "
            f"(missing required prefix)."
        )
    charset_re = rule.get("charset_re")
    if charset_re is not None and not charset_re.match(value):
        raise ValueError(
            f"Secret '{name}' failed validation: disallowed characters present."
        )
    return value


def validate_env_secret(name: str) -> str:
    """
    Strictly validate a secret-bearing environment variable at load time.

    Reads the variable from the environment, applies the hardened
    _get_secret() sanity checks (length, control characters), then applies
    the registered strict rule via validate_secret().

    Returns the validated value, or an empty string when the variable is
    unset. Raises ValueError (message never contains the raw value) when
    the value is malformed — the caller must treat the secret as unset
    and refuse to use it.
    """
    if name not in SECRET_BEARING_KEYS:
        # Not a registered secret-bearing key; nothing strict to enforce.
        return os.environ.get(name, "")
    value = _get_secret(name)
    return validate_secret(name, value)


def validate_env_secrets(names=None) -> dict:
    """
    Batch-validate secret-bearing environment variables at startup.

    Iterates the secret-bearing key registry (or the provided iterable of
    names, intersected with the registry), calls validate_env_secret()
    per key, and aggregates all failures into a single structured error
    so misconfiguration fails fast with one clear report.

    Returns a dict {name: validated_value} for every key that passed
    (unset keys map to ""). Raises RuntimeError with a redacted,
    structured summary when any key fails validation. Error messages
    never contain raw secret values.
    """
    registry = SECRET_BEARING_KEYS
    if names is None:
        targets = sorted(registry)
    else:
        targets = [n for n in names if n in registry]

    validated = {}
    failures = {}
    for name in targets:
        try:
            validated[name] = validate_env_secret(name)
        except ValueError as exc:
            # Store the redacted form of the current value for diagnostics.
            failures[name] = {
                "error": str(exc),
                "value": redact_secret(name, os.environ.get(name, "")),
            }

    if failures:
        # Use safe_log so no secret material can leak into the summary.
        summary = safe_log(
            "Batch secret validation failed for {count} key(s): {keys}",
            count=len(failures),
            keys=", ".join(sorted(failures)),
        )
        raise RuntimeError(
            summary + " :: " + json.dumps(failures, sort_keys=True)
        )
    return validated


# ---------------------------------------------------------------------------
# Import-time batch validation
# ---------------------------------------------------------------------------
# Run the batch validator once at module import against the full
# secret-bearing key registry. A malformed secret fails fast here with a
# redacted, structured report (no raw values ever reach the traceback).
try:
    VALIDATED_SECRETS = validate_env_secrets()
except RuntimeError as _secret_error:
    # Re-raise with a clear, non-leaking message; the RuntimeError from
    # validate_env_secrets already contains only redacted diagnostics.
    raise RuntimeError(
        "config.py: refusing to initialize with invalid secrets. "
        f"{_secret_error}"
    ) from None

WORKSPACE_DIR = os.path.abspath(os.path.dirname(__file__))

# Исправлено: заменена несуществующая google/gemini-2.0-flash-001 на рабочую deepseek/deepseek-chat
DEFAULT_MODELS = {
    "researcher": "deepseek/deepseek-chat",
    "editor": "deepseek/deepseek-chat",
    "reviewer": "deepseek/deepseek-chat",
}
DEFAULT_PRICING = {
    "prompt_price_per_1m": 0.14,
    "completion_price_per_1m": 0.28,
    "context_length": 1_000_000,
}

# Исправлен путь импорта: из папки tools/
try:
    from tools.llm_selector import get_agent_bindings as _get_agent_bindings, get_model_details as _get_model_details
except ImportError:
    try:
        from llm_selector import get_agent_bindings as _get_agent_bindings, get_model_details as _get_model_details
    except ImportError:
        _get_agent_bindings = None
        _get_model_details = None

ACTIVE_MODELS_FILE = os.path.join(WORKSPACE_DIR, "active_models.json")
MODELS_CACHE_FILE = os.path.join(WORKSPACE_DIR, "models_cache.json")


# Model IDs must be provider/name form with a safe charset; this blocks
# path traversal or header injection through a poisoned models cache.
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


def _is_valid_model_id(model_id) -> bool:
    return (
        isinstance(model_id, str)
        and bool(_MODEL_ID_RE.match(model_id))
        and ".." not in model_id
    )


def _load_legacy_active_models() -> dict:
    if os.path.exists(ACTIVE_MODELS_FILE):
        try:
            with open(ACTIVE_MODELS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {
                    agent: data.get(agent, DEFAULT_MODELS[agent])
                    for agent in DEFAULT_MODELS
                    if _is_valid_model_id(data.get(agent, DEFAULT_MODELS[agent]))
                }
        except Exception:
            pass
    return dict(DEFAULT_MODELS)


def _load_legacy_model_specs(model_id: str) -> dict:
    if os.path.exists(MODELS_CACHE_FILE):
        try:
            with open(MODELS_CACHE_FILE, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            data = cached_data.get("data", cached_data) if isinstance(cached_data, dict) else cached_data
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict) or item.get("id") != model_id:
                        continue
                    if not _is_valid_model_id(item.get("id")):
                        continue
                    pricing = item.get("pricing", {}) or {}
                    if not isinstance(pricing, dict):
                        pricing = {}
                    try:
                        prompt_str = str(pricing.get("prompt", "0") or "0")
                        completion_str = str(pricing.get("completion", "0") or "0")
                        prompt_price = float(prompt_str) * 1_000_000
                        completion_price = float(completion_str) * 1_000_000
                        # Reject negative or absurd prices (poisoned cache).
                        if not (0 <= prompt_price <= 10_000) or not (0 <= completion_price <= 10_000):
                            raise ValueError
                        context_length = item.get("context_length", DEFAULT_PRICING["context_length"])
                        if not isinstance(context_length, int) or context_length <= 0:
                            context_length = DEFAULT_PRICING["context_length"]
                    except (TypeError, ValueError):
                        continue
                    return {
                        "prompt_price_per_1m": prompt_price if prompt_str not in ("", "0") else DEFAULT_PRICING["prompt_price_per_1m"],
                        "completion_price_per_1m": completion_price if completion_str not in ("", "0") else DEFAULT_PRICING["completion_price_per_1m"],
                        "context_length": context_length,
                    }
        except Exception:
            pass
    return dict(DEFAULT_PRICING)


def get_active_model(agent: str) -> str:
    """Динамический поиск активной модели для агента."""
    if not isinstance(agent, str) or not agent:
        return "deepseek/deepseek-chat"
    if _get_agent_bindings is not None:
        try:
            bindings = _get_agent_bindings()
            if isinstance(bindings, dict) and agent in bindings:
                candidate = bindings[agent]
                if _is_valid_model_id(candidate):
                    return candidate
        except Exception:
            pass
    # Безопасный откат
    fallback = _load_legacy_active_models().get(agent, DEFAULT_MODELS.get(agent, "deepseek/deepseek-chat"))
    return fallback if _is_valid_model_id(fallback) else "deepseek/deepseek-chat"

get_agent_model = get_active_model


def get_model_specs(model_id: str) -> dict:
    if not _is_valid_model_id(model_id):
        return dict(DEFAULT_PRICING)
    if _get_model_details is not None:
        try:
            details = _get_model_details(model_id)
            if isinstance(details, dict):
                def _safe_price(key: str) -> float:
                    try:
                        val = float(details.get(key, DEFAULT_PRICING[key]))
                        return val if 0 <= val <= 10_000 else DEFAULT_PRICING[key]
                    except (TypeError, ValueError):
                        return DEFAULT_PRICING[key]

                context_length = details.get("context_length", DEFAULT_PRICING["context_length"])
                if not isinstance(context_length, int) or context_length <= 0:
                    context_length = DEFAULT_PRICING["context_length"]
                return {
                    "prompt_price_per_1m": _safe_price("price_input_1m"),
                    "completion_price_per_1m": _safe_price("price_output_1m"),
                    "context_length": context_length,
                }
        except Exception:
            pass
    return _load_legacy_model_specs(model_id)


def get_current_price(agent: str) -> tuple:
    model_id = get_active_model(agent)
    specs = get_model_specs(model_id)
    return specs["prompt_price_per_1m"], specs["completion_price_per_1m"], specs["context_length"]

# Константы бюджета и итераций
MAX_BUDGET_USD = 5
MAX_ITERATIONS = 8       # вместо 10
HITL_EVERY_N_CYCLES = 3
PLAN_CHUNK_THRESHOLD = 100

# ============================================================================
# Lesson Memory Bridge (VegeX memory subsystem)
# ============================================================================
# The bridge syncs JSON lesson files into a SQLite A-Score index at runtime.
# JSON remains the source of truth for content; SQLite holds ranking signals.

LESSONS_MEMORY_ENABLED = os.getenv("VEGEX_LESSONS_MEMORY", "1") == "1"
LESSONS_ROOT = os.path.join(WORKSPACE_DIR, "lessons")
LESSONS_MEMORY_DIR = os.path.join(WORKSPACE_DIR, ".vegex")
LESSONS_MEMORY_DB_PATH = os.path.join(LESSONS_MEMORY_DIR, "lessons.db")
def _safe_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    """Parse an integer env var with bounds; falls back to default on abuse."""
    raw = os.getenv(name, "")
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    if val < minimum or val > maximum:
        return default
    return val


LESSONS_EMBEDDING_DIM = _safe_int_env("VEGEX_LESSON_EMBED_DIM", 256, 1, 4096)

# Retrieval defaults
LESSONS_RETRIEVAL_LIMIT = 5
LESSONS_RETRIEVAL_STAGES_RESEARCHER = ("promoted", "candidates", "observations")
LESSONS_RETRIEVAL_STAGES_CONSUMER = ("promoted",)

# A-Score hyperparameters for the lesson ranker (see memory/ranker.py)
LESSONS_RANKING_ALPHA = 1.0
LESSONS_RANKING_BETA_PROTECTION = 2.0
LESSONS_RANKING_LAMBDA_BASE = 0.01
LESSONS_RANKING_EPSILON = 1e-5

# RSI mode: active project is DifferiX itself. Enables diff-based
# security scanning instead of full-content scanning.
RSI_MODE = os.getenv("VEGEX_RSI_MODE", "0") == "1"

# ============================================================================
# Embedding provider (VegeX memory subsystem)
# ============================================================================
# Provider selection: "auto" | "openrouter" | "local" | "hash"
#   auto       → OpenRouter if OPENROUTER_API_KEY is set, else local, else hash
#   openrouter → require OPENROUTER_API_KEY (same key as chat completions)
#   local      → require fastembed installed (offline)
#   hash       → zero-dependency bag-of-words (low quality; emergency only)
EMBEDDING_PROVIDER = os.getenv("VEGEX_EMBEDDING_PROVIDER", "auto")
EMBEDDING_PREFER_LOCAL = os.getenv("VEGEX_EMBEDDING_PREFER_LOCAL", "0") == "1"

# Target vector dimension. text-embedding-3-small supports the `dimensions`
# parameter (truncation); 512 balances quality and storage. Local MiniLM
# is fixed at 384 and ignores this.
EMBEDDING_DIM = _safe_int_env("VEGEX_EMBEDDING_DIM", 512, 1, 4096)

# OpenRouter embedding model ID (provider-prefixed).
OPENROUTER_EMBEDDING_MODEL = os.getenv(
    "VEGEX_OPENROUTER_EMBED_MODEL", "openai/text-embedding-3-small"
)

# ---------------------------------------------------------------------------
# Reflection — periodic self-analysis of recent failures
# ---------------------------------------------------------------------------
REFLECTION_INTERVAL = _safe_int_env("VEGEX_REFLECTION_INTERVAL", 5, 1, 1000)
REFLECTION_MODEL = os.getenv("VEGEX_REFLECTION_MODEL", "")  # empty → top-tier
if REFLECTION_MODEL and not _is_valid_model_id(REFLECTION_MODEL):
    REFLECTION_MODEL = ""
REFLECTION_MAX_OBSERVATIONS = 3
REFLECTION_MIN_OCCURRENCES = 2

# Note: no separate key is needed. The OpenRouterEmbeddingProvider uses
# OPENROUTER_API_KEY (defined at the top of this module).

# ============================================================================
# Tiered Context Assembly (TCA)
# ============================================================================
# Context is assembled with a fixed token budget per role. Tiers are packed
# in priority order (T0_STATIC -> T5_FEEDBACK). When the budget is exhausted,
# lower-priority content is degraded to its fallback or dropped entirely.
#
# See tools/context_assembler.py for the tier hierarchy.
#
# Setting a budget to a huge value effectively disables TCA truncation for
# that role (all content is included as-is).
TCA_ENABLED = os.getenv("VEGEX_TCA_ENABLED", "1") == "1"

TOKEN_BUDGET_EDITOR = _safe_int_env("VEGEX_BUDGET_EDITOR", 18000, 2000, 200000)
TOKEN_BUDGET_REVIEWER = _safe_int_env("VEGEX_BUDGET_REVIEWER", 14000, 2000, 200000)
TOKEN_BUDGET_RESEARCHER = _safe_int_env("VEGEX_BUDGET_RESEARCHER", 12000, 2000, 200000)

# Fraction of budget actually used (headroom for token estimate error).
TCA_SAFETY_MARGIN = 0.9

# Include feedback (past errors) on iteration 0? Default False — nothing
# useful on the first try, saves ~1-2k tokens per cycle.
TCA_INCLUDE_FEEDBACK_ON_FIRST_CYCLE = False

# Print one-line TCA summary after every assemble(). Set to 0 in production
# once budgets are tuned.
TCA_LOG_SUMMARY = True

# ============================================================================
# RSI Mode Safety (Phase 1.3)
# ============================================================================
# These files are the "brain" and "immune system" of DifferiX. In RSI mode,
# the agent can *propose* changes to them, but they will NOT be applied
# automatically. They are saved to .vegex/pending_critical_changes/ for human review.
RSI_CRITICAL_FILES = {
    "main.py",
    "config.py",
    "tools/security_filter.py",
    "tools/file_manager.py",
    "tools/state_manager.py",
    "tools/security/verification_firewall.py",
    "tools/security/behavior_guard.py",
}