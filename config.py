import os
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("GEMINI_API_KEY")
OPENROUTER_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Active Google AI Studio models
MODEL_RESEARCHER = "gemini-3.5-flash"
MODEL_EDITOR = "gemini-3.5-flash"
MODEL_REVIEWER = "gemini-3.5-flash"

# Token pricing (Free tier = 0.0)
PRICE_INPUT_1M = 0.0
PRICE_OUTPUT_1M = 0.0
MAX_ITERATIONS = 5
MAX_BUDGET_USD = 1.0

PROJECTS = {
    # "1": {
    #     "name": "Victorian Overhaul",
    #     "path": r"C:\Program Files (x86)\Steam\steamapps\common\RimWorld\Mods\Victorian overhaul",
    #     "lang": "csharp",
    # },
    "2": {
        "name": "Function",
        "path": r"C:\projects-py\function_1.1",
        "lang": "python",
    },
}