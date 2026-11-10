import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)

import config
from openai import OpenAI

api_key = config.OPENROUTER_API_KEY

print(f"Checking API key: {'FOUND' if api_key else 'NOT FOUND'}")

if not api_key:
    print("Error: API key not found")
    exit(1)

client = OpenAI(
    base_url=config.OPENROUTER_BASE_URL,
    api_key=api_key,
)

print(f"Sending test message to LLM: {config.MODEL_REVIEWER}...")

try:
    completion = client.chat.completions.create(
        model=config.MODEL_RESEARCHER,
        messages=[
            {"role": "user", "content": "Write only 1 word if you can read it."}
        ]
    )
    
    answer = completion.choices[0].message.content
    print(f"\nLLM returned an answer successfully ({config.MODEL_RESEARCHER}): {answer.strip()}")

except Exception as e:
    print(f"\nConnection or API key error: \n{e}")