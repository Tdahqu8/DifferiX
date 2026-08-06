import os
from dotenv import load_dotenv
load_dotenv() # Подгружает .env автономом прямо внутри конфига

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("GEMINI_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Heavy reasoning for deep repository research
MODEL_RESEARCHER = "nvidia/nemotron-3-ultra-550b-a55b:free"

# Strongest free instruction-following model currently available in your list
MODEL_EDITOR = "google/gemma-4-26b-a4b-it:free"

# Reasoning model for catching edge cases and logic bugs
MODEL_REVIEWER = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"

# Цены (для бесплатного тарифа 0)
PRICE_INPUT_1M = 0.0
PRICE_OUTPUT_1M = 0.0
MAX_ITERATIONS = 5
MAX_BUDGET_USD = 1.0

PROJECTS = {
    "1": {
        "name": "Victorian Overhaul",
        "path": "C:\\Program Files (x86)\\Steam\\steamapps\\common\\RimWorld\\Mods\\Victorian overhaul",
        "lang": "csharp"
    },
    "2": {
        "name": "Function 1.1",
        "path": "C:\\projects-py\\function_1.1",
        "lang": "python"
    }
}