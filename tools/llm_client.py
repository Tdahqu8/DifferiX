import os
import time
import json
import httpx
import openai
from openai import OpenAI
import config
from tools.llm_selector import (
    get_agent_fallback_chain, 
    resolve_model_for_task, 
    CONFIG_FILE,
    get_dynamic_routing_enabled,
    get_agent_bindings
)

RECOVERABLE_ERRORS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
    openai.RateLimitError,
    httpx.RequestError,
)

def safe_chat_completion(
    role_or_model: str,
    messages: list,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    extra_body: dict = None,
    retries: int = 3,
    model: str = None,
    task_spec: str = None,
):
    api_key = getattr(config, "OPENROUTER_API_KEY", None) or os.getenv("OPENROUTER_API_KEY", "dummy")
    base_url = getattr(config, "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    client = OpenAI(base_url=base_url, api_key=api_key, timeout=120.0)

    primary_model = None
    is_dynamic = get_dynamic_routing_enabled()
    bindings = get_agent_bindings()

    if model:
        primary_model = model
    elif role_or_model in bindings and os.path.exists(CONFIG_FILE):
        # Manual binding exists in config file
        primary_model = bindings[role_or_model]
    elif is_dynamic and task_spec and str(task_spec).strip() and role_or_model in ["researcher", "editor", "reviewer"]:
        # Dynamic routing is enabled and we have task spec
        try:
            primary_model = resolve_model_for_task(task_spec, role_or_model)
        except Exception as exc:
            print(f"[LLM Client] Dynamic routing failed ({exc}); falling back to bindings.")
            primary_model = bindings.get(role_or_model, role_or_model)
    else:
        # Fallback to bindings
        primary_model = bindings.get(role_or_model, role_or_model)

    if primary_model:
        models_to_try = [primary_model]
        for fallback in get_agent_fallback_chain(role_or_model):
            if fallback not in models_to_try:
                models_to_try.append(fallback)
    else:
        models_to_try = get_agent_fallback_chain(role_or_model)

    last_exception = None

    for model_name in models_to_try:
        for attempt in range(retries):
            try:
                kwargs = {
                    "model": model_name,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if extra_body:
                    kwargs["extra_body"] = extra_body

                print(
                    f"[LLM Router] role={role_or_model} "
                    f"task_spec={(task_spec or '')[:40]!r} "
                    f"dynamic={is_dynamic} primary={primary_model} trying={model_name}"
                )

                response = client.chat.completions.create(**kwargs)
                provider = "OpenRouter" if "openrouter" in base_url.lower() else "Local/Ollama"
                return response, provider

            except RECOVERABLE_ERRORS as e:
                last_exception = e
                time.sleep(2 ** attempt)
            except openai.APIStatusError as e:
                last_exception = e
                print(f"[LLM Client] Model '{model_name}' returned HTTP {e.status_code}. Switching to fallback...")
                break
            except Exception as e:
                last_exception = e
                print(f"[LLM Client] Unexpected error with '{model_name}': {e}")
                break

    raise RuntimeError(f"LLM Chat Completion failed for all models {models_to_try}: {last_exception}")