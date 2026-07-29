import json
from openai import OpenAI
import config

client = OpenAI(
    base_url=config.OPENROUTER_BASE_URL,
    api_key=config.OPENROUTER_API_KEY,
)

def run_editor(state, project_files=""):
    system_prompt = f"""Ты — ИИ-Редактор кода. Напиши чистый код/XML на основе плана Исследователя из state.json.
Не пиши никаких разговоров и сообщений. Выдай СТРОГО JSON-объект с ключами:
- "target_file": путь к файлу
- "action": действие ("create" или "modify")
- "code": готовый код или XML

Список файлов проекта для точных путей:
{project_files}"""

    response = client.chat.completions.create(
        model=config.MODEL_EDITOR,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"State: {json.dumps(state, ensure_ascii=False)}"}
        ],
        response_format={"type": "json_object"},
        temperature=0.1
    )
    
    result_text = response.choices[0].message.content
    result = json.loads(result_text)
    
    state["generated_code"] = result
    state["current_step"] = "review"
    
    # Возвращаем метаданные о токенах для main.py
    usage_meta = type('Meta', (), {
        'prompt_token_count': response.usage.prompt_tokens,
        'candidates_token_count': response.usage.completion_tokens
    })
    
    return state, usage_meta