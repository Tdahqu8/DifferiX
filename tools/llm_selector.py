import os
import json
import httpx
import config

CACHE_FILE = "openrouter_models_cache.json"
CONFIG_FILE = "models_config.json"
TIERS_FILE = "model_tiers.json"

DEFAULT_AGENTS = ["researcher", "editor", "reviewer"]

# Динамическая загрузка тиров
def _load_tiers() -> dict:
    default_tiers = {
        "high": ["anthropic/claude-3.5-sonnet", "openai/gpt-4o"],
        "medium": ["deepseek/deepseek-chat", "google/gemini-2.5-flash"],
        "low": ["meta-llama/llama-3-8b-instruct", "deepseek/deepseek-chat"],
    }
    if os.path.exists(TIERS_FILE):
        try:
            with open(TIERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "high" in data and "medium" in data and "low" in data:
                    return data
        except Exception:
            pass
    return default_tiers

def get_dynamic_routing_enabled() -> bool:
    """Возвращает True, если динамический роутинг включен."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return cfg.get("dynamic_routing", True)
        except Exception:
            pass
    return True

def set_dynamic_routing_enabled(enabled: bool) -> None:
    """Сохраняет флаг динамического роутинга в models_config.json."""
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
    cfg["dynamic_routing"] = enabled
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[LLM Selector] Warning: Failed to save routing flag: {e}")
        
DEFAULT_BINDINGS = {
    "researcher": "deepseek/deepseek-coder",
    "editor": "deepseek/deepseek-coder",
    "reviewer": "deepseek/deepseek-coder",
}

DEFAULT_FALLBACKS = {
    "researcher": ["google/gemini-3.8-flash", "deepseek/deepseek-v4-flash-latest"],
    "editor": ["deepseek/deepseek-v4-flash-latest", "openai/gpt-4o-mini"],
    "reviewer": ["z-ai/glm-4.7-flash", "google/gemini-3.8-flash"],
}

DEFAULT_CATALOG = {
    "anthropic/claude-3.5-sonnet:beta": {
        "name": "Anthropic: Claude 3.5 Sonnet (Beta)",
        "context_length": 200000,
        "price_input_1m": 3.0,
        "price_output_1m": 15.0,
    },
    "anthropic/claude-3.7-sonnet": {
        "name": "Anthropic: Claude 3.7 Sonnet",
        "context_length": 200000,
        "price_input_1m": 3.0,
        "price_output_1m": 15.0,
    },
    "google/gemini-2.5-pro": {
        "name": "Google: Gemini 2.5 Pro",
        "context_length": 1000000,
        "price_input_1m": 1.25,
        "price_output_1m": 5.0,
    },
    "ollama/qwen2.5-coder": {
        "name": "Ollama: Qwen 2.5 Coder",
        "context_length": 32768,
        "price_input_1m": 0.0,
        "price_output_1m": 0.0,
    },
}


def fetch_openrouter_models(api_key: str = None) -> list[dict]:
    """Запрашивает сырой список моделей напрямую с OpenRouter API."""
    key = api_key or getattr(config, "OPENROUTER_API_KEY", None) or os.getenv("OPENROUTER_API_KEY")
    url = "https://openrouter.ai/api/v1/models"
    headers = {}
    if key and key != "dummy":
        headers["Authorization"] = f"Bearer {key}"

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                return data.get("data", [])
    except Exception:
        pass
    return []


def sync_model_catalog() -> dict[str, dict]:
    """Загружает каталог OpenRouter и сохраняет кэш."""
    raw_models = fetch_openrouter_models()
    catalog = dict(DEFAULT_CATALOG)

    if raw_models:
        for item in raw_models:
            m_id = item.get("id")
            if not m_id:
                continue

            pricing = item.get("pricing", {})
            try:
                p_in = float(pricing.get("prompt", 0) or 0) * 1_000_000
            except (ValueError, TypeError):
                p_in = 0.0

            try:
                p_out = float(pricing.get("completion", 0) or 0) * 1_000_000
            except (ValueError, TypeError):
                p_out = 0.0

            catalog[m_id] = {
                "name": item.get("name", m_id),
                "context_length": item.get("context_length", 0) or 0,
                "price_input_1m": p_in,
                "price_output_1m": p_out,
            }

    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[LLM Selector] Warning: Failed to write catalog cache: {e}")

    return catalog


def get_cached_models(force_refresh: bool = False) -> dict[str, dict]:
    """Возвращает кэшированный словарь моделей."""
    if not force_refresh and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and len(data) > 0:
                    return data
        except Exception:
            pass

    return sync_model_catalog()


def get_model_details(model_id: str) -> dict:
    """Возвращает спецификацию конкретной модели."""
    models = get_cached_models()
    return models.get(
        model_id,
        {
            "name": model_id,
            "context_length": 0,
            "price_input_1m": 0.0,
            "price_output_1m": 0.0,
        },
    )


def get_agent_bindings() -> dict[str, str]:
    """Возвращает привязки агентов к моделям, объединяя models_config.json и active_models.json."""
    bindings = dict(DEFAULT_BINDINGS)

    # First load legacy active_models.json if it exists
    legacy_file = "active_models.json"
    if os.path.exists(legacy_file):
        try:
            with open(legacy_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for agent in DEFAULT_AGENTS:
                        if agent in data:
                            bindings[agent] = data[agent]
        except Exception:
            pass

    # Then load models_config.json which takes precedence
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    loaded_bindings = data.get("bindings", data)
                    if isinstance(loaded_bindings, dict):
                        bindings.update(loaded_bindings)
        except Exception:
            pass

    # Finally check for environment overrides
    for agent in DEFAULT_AGENTS:
        env_attr = f"MODEL_{agent.upper()}"
        if hasattr(config, env_attr):
            val = getattr(config, env_attr)
            if val:
                bindings[agent] = val

    return bindings


def save_agent_bindings(bindings: dict[str, str]) -> None:
    """Сохраняет привязки агентов в models_config.json."""
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if not isinstance(cfg, dict):
                    cfg = {}
        except Exception:
            cfg = {}

    cfg["bindings"] = bindings
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[LLM Selector] Warning: Failed to save agent bindings: {e}")


def get_agent_fallback_chain(role_or_model: str) -> list[str]:
    """
    Формирует цепочку фоллбэков для роли ИЛИ для конкретного имени модели.
    Автоматически подменяет недействительный 'anthropic/claude-3.5-sonnet'.
    """
    key = (role_or_model or "").lower()
    bindings = get_agent_bindings()

    if key in DEFAULT_AGENTS:
        primary = bindings.get(key, "google/gemini-2.5-pro")
        defaults = DEFAULT_FALLBACKS.get(key, ["google/gemini-2.5-pro"])
        raw_chain = [primary] + [m for m in defaults if m != primary]
    else:
        # Если передано имя модели, делаем его первым, а затем добавляем общие фоллбэки
        raw_chain = [role_or_model, "deepseek/deepseek-coder", "google/gemini-2.5-flash"]

    clean_chain = []
    for m in raw_chain:
        if m == "anthropic/claude-3.5-sonnet":
            m = "anthropic/claude-3.5-sonnet:beta"
        if m not in clean_chain:
            clean_chain.append(m)

    return clean_chain


def resolve_active_model(role: str, attempt: int = 0) -> str:
    """Выбирает модель для вызова с учетом номера попытки."""
    chain = get_agent_fallback_chain(role)
    if attempt < len(chain):
        return chain[attempt]
    return chain[-1]


def set_active_model(role: str, model_name: str) -> None:
    """Устанавливает модель для роли и сохраняет конфигурацию."""
    bindings = get_agent_bindings()
    bindings[role.lower()] = model_name
    save_agent_bindings(bindings)


def get_manual_binding(role: str) -> str:
    """Returns the manually set binding for a role from models_config.json, or empty string if not set."""
    if not os.path.exists(CONFIG_FILE):
        return ""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            bindings = data.get("bindings", data)
            return bindings.get(role, "")
    except Exception:
        return ""

def get_active_models() -> dict[str, str]:
    """Возвращает активные модели для всех основных агентов."""
    bindings = get_agent_bindings()
    return {role: bindings.get(role, "google/gemini-2.5-pro") for role in DEFAULT_AGENTS}

def resolve_model_for_task(task_prompt: str, default_model: str = None) -> str:
    """
    Integration entrypoint for dynamic LLM assignment.
    """
    try:
        complexity = assess_task_complexity(task_prompt)
        model = select_model_for_task(complexity)
        if model:
            return model
    except Exception as e:
        print(f"[LLM Selector] Warning: resolve_model_for_task failed: {e}")

    if default_model:
        return default_model

    try:
        return select_model_for_task("medium")
    except Exception:
        return "google/gemini-2.5-pro"

def assess_task_complexity(task_prompt: str) -> str:
    """
    Assesses task complexity based on the prompt.
    Returns 'low', 'medium', or 'high'.
    """
    if not task_prompt:
        return "low"

    prompt_len = len(task_prompt)
    lower_prompt = task_prompt.lower()

    high_keywords = ["refactor", "implement", "add feature", "design", "architecture", "integrate", "from scratch"]
    medium_keywords = ["fix", "debug", "update", "improve", "add test", "style", "enhance"]

    if any(kw in lower_prompt for kw in high_keywords) or prompt_len > 400:
        return "high"

    if any(kw in lower_prompt for kw in medium_keywords) or prompt_len > 100:
        return "medium"

    return "low"

def select_model_for_task(complexity: str) -> str:
    """
    Selects the best available model for a given complexity tier.
    """
    tiers = _load_tiers()
    tier = complexity if complexity in tiers else "medium"
    preferred_models = tiers[tier]
    all_models = get_cached_models()

    for model_id in preferred_models:
        if model_id in all_models:
            return model_id

    for fallback_tier in ["medium", "high", "low"]:
        for model_id in tiers.get(fallback_tier, []):
            if model_id in all_models:
                return model_id

    if all_models:
        return next(iter(all_models))
    
    return "google/gemini-2.5-pro"