import sys
import os
import json
import shlex
from typing import Dict, Any, List, Optional, Union

# NOTE: the actual data/caching layer lives in llm_selector.py (fetches and
# caches the OpenRouter catalog, and now also owns agent<->model bindings).
# This used to point at a "core.model_fetcher" / "model_fetcher" module that
# never existed in this project, which meant every import silently failed
# and fell through to a hardcoded stub -- complete with a fake cache path
# llm_selector.py never wrote to, and a fake agent roster ("director",
# "scriptwriter", "critic", "visual_designer", "voiceover") that has nothing
# to do with the real pipeline. That's why `llm` showed no context/pricing:
# it was reading an empty stub, not your real catalog.
try:
    from core.llm_selector import (
        fetch_openrouter_models,
        get_cached_models,
        get_model_details,
        sync_model_catalog,
        get_agent_bindings,
        save_agent_bindings,
        DEFAULT_AGENTS,
    )
except ImportError:
    from tools.llm_selector import (
        fetch_openrouter_models,
        get_cached_models,
        get_model_details,
        sync_model_catalog,
        get_agent_bindings,
        save_agent_bindings,
        DEFAULT_AGENTS,
    )


# Terminal ANSI Color codes
ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_CYAN = "\033[36m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"
ANSI_MAGENTA = "\033[35m"
ANSI_GRAY = "\033[90m"


def format_price(val: Any) -> str:
    """Format an already-per-1M-token price (as llm_selector's catalog stores it)."""
    try:
        per_m = float(val)
        if per_m == 0:
            return "$0.00"
        if per_m < 0.01:
            return f"${per_m:.4f}"
        return f"${per_m:.2f}"
    except (ValueError, TypeError):
        return "N/A"


def format_context(ctx: Any) -> str:
    """Format context length into human-readable representation."""
    try:
        c = int(ctx)
        if c >= 1_000_000:
            return f"{c / 1_000_000:.1f}M"
        if c >= 1000:
            return f"{c // 1000}k"
        return str(c)
    except (ValueError, TypeError):
        return "N/A"


def print_header(title: str) -> None:
    print(f"\n{ANSI_BOLD}{ANSI_CYAN}=== {title} ==={ANSI_RESET}\n")


def display_llm_status() -> None:
    """Display current agent model bindings with context and pricing specifications."""
    print_header("DifferiX Multi-Agent LLM Model Bindings")

    bindings = get_agent_bindings()
    cached_models = get_cached_models()  # dict: model_id -> spec

    header_fmt = "{:<16} {:<38} {:<12} {:<12} {:<12}"
    print(
        ANSI_BOLD
        + header_fmt.format("AGENT", "MODEL ID", "CONTEXT", "INPUT/1M", "OUTPUT/1M")
        + ANSI_RESET
    )
    print("-" * 94)

    if not cached_models:
        print(f"{ANSI_YELLOW}[!] No local catalog cached yet. Run 'llm sync' to fetch context/pricing data.{ANSI_RESET}\n")

    for agent, model_id in bindings.items():
        model_info = cached_models.get(model_id)
        if model_info:
            context = format_context(model_info.get("context_length", "N/A"))
            in_cost = format_price(model_info.get("price_input_1m", 0))
            out_cost = format_price(model_info.get("price_output_1m", 0))
        else:
            context = "Unknown"
            in_cost = "Unknown"
            out_cost = "Unknown"

        print(
            f"{ANSI_GREEN}{agent:<16}{ANSI_RESET} "
            f"{ANSI_BOLD}{model_id:<38}{ANSI_RESET} "
            f"{context:<12} "
            f"{in_cost:<12} "
            f"{out_cost:<12}"
        )

    print("-" * 94)
    print(f"{ANSI_GRAY}Run 'llm <agent> select' to change an agent's model or 'llm sync' to update catalog.{ANSI_RESET}\n")


def sync_catalog_command() -> None:
    """Force sync local catalog from OpenRouter API."""
    print(f"{ANSI_CYAN}[*] Synchronizing OpenRouter catalog...{ANSI_RESET}")
    models = sync_model_catalog()
    print(f"{ANSI_GREEN}[+] Catalog updated successfully. Total models: {len(models)}{ANSI_RESET}\n")


def filter_models(
    models: Dict[str, Dict[str, Any]],
    query: Optional[str] = None,
    provider: Optional[str] = None,
    max_input_price: Optional[float] = None,
    min_context: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Filter the cached catalog (model_id -> spec dict) by provider, query
    term, price, and context window. Returns a list of specs with 'id'
    folded in, sorted by model_id, for stable display ordering."""
    filtered = []
    for m_id, spec in models.items():
        m_id_l = m_id.lower()
        m_name = str(spec.get("name", "")).lower()
        context_len = spec.get("context_length", 0) or 0
        # Catalog already stores price per 1M tokens (see llm_selector.py) --
        # no per-token conversion here, unlike the raw OpenRouter response.
        prompt_cost = float(spec.get("price_input_1m", 0) or 0)

        if query:
            q = query.lower()
            if q not in m_id_l and q not in m_name:
                continue

        if provider:
            p = provider.lower()
            if not m_id_l.startswith(p + "/") and p not in m_id_l:
                continue

        if max_input_price is not None and prompt_cost > max_input_price:
            continue

        if min_context is not None and context_len < min_context:
            continue

        entry = dict(spec)
        entry["id"] = m_id
        filtered.append(entry)

    filtered.sort(key=lambda e: e["id"])
    return filtered


def interactive_select_model(agent_name: str) -> None:
    """Interactive filter and selection prompt for an agent."""
    models = get_cached_models()
    if not models:
        print(f"{ANSI_YELLOW}[*] No local catalog found. Fetching from OpenRouter...{ANSI_RESET}")
        models = sync_model_catalog()

    if not models:
        print(f"{ANSI_RED}[!] Error: Failed to retrieve models from catalog.{ANSI_RESET}")
        return

    bindings = get_agent_bindings()
    current_model = bindings.get(agent_name, "None")

    print_header(f"Model Selection for Agent: {agent_name.upper()}")
    print(f"Current Binding: {ANSI_GREEN}{current_model}{ANSI_RESET}\n")

    query = ""
    provider = ""
    max_price = None
    min_context = None

    while True:
        results = filter_models(
            models,
            query=query if query else None,
            provider=provider if provider else None,
            max_input_price=max_price,
            min_context=min_context,
        )

        print(f"\n{ANSI_BOLD}Filtered Models ({len(results)} found):{ANSI_RESET}")
        print(f"Filters -> Query: '{query or 'all'}', Provider: '{provider or 'all'}', Max $/1M: {max_price or 'any'}, Min Ctx: {min_context or 'any'}")
        print("-" * 90)
        print(
            ANSI_BOLD
            + "{:<4} {:<42} {:<12} {:<12} {:<12}".format(
                "#", "MODEL ID", "CONTEXT", "INPUT/1M", "OUTPUT/1M"
            )
            + ANSI_RESET
        )
        print("-" * 90)

        display_slice = results[:20]
        for idx, m in enumerate(display_slice, 1):
            m_id = m.get("id", "")
            ctx = format_context(m.get("context_length", 0))
            in_p = format_price(m.get("price_input_1m", 0))
            out_p = format_price(m.get("price_output_1m", 0))

            highlight = ANSI_GREEN if m_id == current_model else ""
            reset = ANSI_RESET if m_id == current_model else ""

            print(
                f"{highlight}{idx:<4} {m_id:<42} {ctx:<12} {in_p:<12} {out_p:<12}{reset}"
            )

        if len(results) > 20:
            print(f"{ANSI_GRAY}... and {len(results) - 20} more models. Refine search query.{ANSI_RESET}")

        print("\nCommands / Options:")
        print("  [1-20]            : Select model by list number")
        print("  /search <term>    : Filter by model name or ID")
        print("  /provider <name>  : Filter by provider (e.g. anthropic, openai, meta-llama, mistralai)")
        print("  /maxprice <num>   : Max prompt price per 1M tokens (e.g. 5.0)")
        print("  /minctx <num>     : Minimum context window (e.g. 128000)")
        print("  /reset            : Clear all filters")
        print("  /cancel           : Exit without changing")

        choice = input(f"\n{ANSI_CYAN}Select option or command > {ANSI_RESET}").strip()

        if not choice or choice.lower() == "/cancel":
            print(f"{ANSI_YELLOW}Selection canceled. Model unchanged.{ANSI_RESET}\n")
            break

        if choice.lower() == "/reset":
            query = ""
            provider = ""
            max_price = None
            min_context = None
            continue

        if choice.startswith("/search"):
            parts = choice.split(maxsplit=1)
            query = parts[1].strip() if len(parts) > 1 else ""
            continue

        if choice.startswith("/provider"):
            parts = choice.split(maxsplit=1)
            provider = parts[1].strip() if len(parts) > 1 else ""
            continue

        if choice.startswith("/maxprice"):
            parts = choice.split(maxsplit=1)
            if len(parts) > 1:
                try:
                    max_price = float(parts[1].strip())
                except ValueError:
                    print(f"{ANSI_RED}Invalid price number.{ANSI_RESET}")
            else:
                max_price = None
            continue

        if choice.startswith("/minctx"):
            parts = choice.split(maxsplit=1)
            if len(parts) > 1:
                try:
                    min_context = int(parts[1].strip())
                except ValueError:
                    print(f"{ANSI_RED}Invalid context length number.{ANSI_RESET}")
            else:
                min_context = None
            continue

        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(display_slice):
                selected = display_slice[idx - 1]
                selected_id = selected.get("id")
                set_agent_model(agent_name, selected_id)
                break
            else:
                print(f"{ANSI_RED}Index out of range. Enter a number between 1 and {len(display_slice)}.{ANSI_RESET}")
        else:
            # Check if user directly typed a model id verbatim
            if choice in models:
                set_agent_model(agent_name, choice)
                break
            else:
                # Default to searching with input
                query = choice


def set_agent_model(agent_name: str, model_id: str) -> None:
    """Set model ID for a specific agent and persist, preserving other bindings."""
    from tools.llm_selector import CONFIG_FILE
    
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if not isinstance(cfg, dict):
                    cfg = {}
        except Exception:
            pass
    
    if "bindings" not in cfg:
        cfg["bindings"] = {}
    
    cfg["bindings"][agent_name] = model_id
    
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"{ANSI_RED}Error saving bindings: {e}{ANSI_RESET}")
        return

    print(f"\n{ANSI_GREEN}[+] Agent '{agent_name}' is now bound to '{model_id}'{ANSI_RESET}\n")


def sync_active_models_specs(agents: Optional[List[str]] = None) -> Dict[str, Any]:
    """Refreshes local pricing/context specs for whichever models are
    currently bound to the active agents, so 'llm status' and cost tracking
    don't keep showing stale/'Unknown' numbers.

    Only triggers a full catalog re-fetch when the local cache is empty or
    is missing a spec for a currently-bound model - cheap to call on every
    run_agent_cycle() start, which is what main.py does.

    `agents` optionally restricts the check to a subset of bound agent keys;
    omit it to check every currently bound agent.
    """
    bindings = get_agent_bindings()
    if agents:
        bindings = {a: m for a, m in bindings.items() if a in agents}

    cached_ids = set(get_cached_models().keys())
    active_ids = {model_id for model_id in bindings.values() if model_id}
    missing_ids = active_ids - cached_ids

    synced = False
    if not cached_ids or missing_ids:
        sync_model_catalog()
        synced = True

    return {
        "agents_checked": list(bindings.keys()),
        "models_checked": list(active_ids),
        "synced": synced,
    }


def handle_llm_command(args: Union[str, List[str], None]) -> None:
    """Main CLI router for 'llm' commands.

    Accepts either a pre-split list of args (["researcher", "select"]) or a
    raw command-line string ("researcher select"), since main.py's REPL
    passes the latter (cmd_line.split(maxsplit=1) leaves the tail as one
    string, and the __main__ entrypoint passes " ".join(sys.argv[2:])).
    Without this, args[0] on a raw string silently returns its first
    character instead of its first word.
    """
    if args is None:
        args = []
    elif isinstance(args, str):
        args = shlex.split(args) if args.strip() else []

    if not args:
        display_llm_status()
        return

    subcmd = args[0].lower()

    if subcmd in ["status", "list", "show"]:
        display_llm_status()
    elif subcmd in ["sync", "refresh", "update"]:
        sync_catalog_command()
    elif subcmd in ["select", "choose", "pick"]:
        if len(args) > 1:
            interactive_select_model(args[1])
        else:
            print("Select an agent:")
            bindings = get_agent_bindings()
            for idx, agent in enumerate(bindings.keys(), 1):
                print(f"  {idx}. {agent}")
            choice = input("Enter agent name or number: ").strip()
            agent_list = list(bindings.keys())
            if choice.isdigit() and 1 <= int(choice) <= len(agent_list):
                interactive_select_model(agent_list[int(choice) - 1])
            elif choice in bindings:
                interactive_select_model(choice)
            else:
                print(f"{ANSI_RED}Invalid agent choice.{ANSI_RESET}")
    elif len(args) >= 2 and args[1].lower() in ["select", "pick", "choose"]:
        agent = args[0]
        interactive_select_model(agent)
    elif len(args) >= 3 and args[1].lower() == "set":
        agent = args[0]
        model_id = args[2]
        set_agent_model(agent, model_id)
    elif len(args) == 2 and args[0] not in ["set", "select", "sync", "status"]:
        set_agent_model(args[0], args[1])
    else:
        print(f"{ANSI_RED}Unknown command: {' '.join(args)}{ANSI_RESET}")
        print("Usage:")
        print("  llm                          Display current bindings")
        print("  llm sync                     Refresh OpenRouter catalog")
        print("  llm <agent> select           Interactive model selector")
        print("  llm <agent> set <model_id>   Direct model assignment")


def main() -> None:
    """Standalone entry point for CLI."""
    args = sys.argv[1:]
    handle_llm_command(args)


if __name__ == "__main__":
    main()