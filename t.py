import os
from pathlib import Path
from dotenv import load_dotenv

# 1. СНАЧАЛА подгружаем .env
env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)

# 2. И ТОЛЬКО ПОТОМ импортируем config!
import config
from openai import OpenAI

api_key = config.OPENROUTER_API_KEY

print(f"🔑 Проверяем ключ OpenRouter из config: {'НАЙДЕН' if api_key else '❌ НЕ НАЙДЕН'}")

if not api_key:
    print("⚠️ Ошибка: Ключ не найден.")
    exit(1)

client = OpenAI(
    base_url=config.OPENROUTER_BASE_URL,
    api_key=api_key,
)

print(f"📡 Отправляем тестовый запрос к модели: {config.MODEL_REVIEWER}...")

try:
    completion = client.chat.completions.create(
        model=config.MODEL_RESEARCHER,
        messages=[
            {"role": "user", "content": "Ответь одним словом: Работает?"}
        ]
    )
    
    answer = completion.choices[0].message.content
    print(f"\n🎉 ЕЕЕЕ! УСПЕХ! Ответ от OpenRouter ({config.MODEL_RESEARCHER}): {answer.strip()}")

except Exception as e:
    print(f"\n❌ ОШИБКА ПОДКЛЮЧЕНИЯ ИЛИ КЛЮЧА:\n{e}")