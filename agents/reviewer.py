import json
from openai import OpenAI
import config

client = OpenAI(
    base_url=config.OPENROUTER_BASE_URL,
    api_key=config.OPENROUTER_API_KEY,
)

def run_reviewer(state):
    system_prompt = """Ты — ИИ-Ревизор. Изучи compiler_output в state.json.
Если там ошибка компиляции, найди ее причину и напиши инструкцию для Редактора, как исправить синтаксис.
Выведи СТРОГО JSON с ключами: "is_approved" (true/false), "verdict" (текст), "fix_instruction" (что исправить)."""

    response = client.chat.completions.create(
        model=config.MODEL_REVIEWER,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"State: {json.dumps(state, ensure_ascii=False)}"}
        ],
        response_format={"type": "json_object"},
        temperature=0.1
    )
    
    result_text = response.choices[0].message.content
    result = json.loads(result_text)
    
    if not result["is_approved"]:
        state["research_data"] += f"\n[Правка Ревизора]: {result['fix_instruction']}"
        state["current_step"] = "edit"
        state["iteration_count"] += 1
    else:
        state["current_step"] = "done"
        
    usage_meta = type('Meta', (), {
        'prompt_token_count': response.usage.prompt_tokens,
        'candidates_token_count': response.usage.completion_tokens
    })
    
    return state, usage_meta