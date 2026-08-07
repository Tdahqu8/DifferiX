import json
from openai import OpenAI
import config

client = OpenAI(
    base_url=config.OPENROUTER_BASE_URL,
    api_key=config.OPENROUTER_API_KEY,
)

def run_reviewer(state):
    system_prompt = """Ты — ИИ-Ревизор (Critic) в системе f(x).

Твоя задача: проверить результат работы Редактора ТОЛЬКО на основе фактов.

Тебе дают:
1. compiler_output / test_output — реальный лог запуска (сборка или тесты)
2. generated_code — что изменил Editor (target_file, action, code)
3. research_data / plan — исходный план
4. project_structure — актуальная структура проекта
5. existing warnings / lessons (если есть)

ЖЁСТКИЕ ПРАВИЛА:
- Ты НЕ имеешь права ставить is_approved=true, если в compiler_output/test_output есть ошибки, traceback, FAILED, error CS, Exception.
- Ты НЕ одобряешь код «на глаз». Источник истины — только лог запуска.
- Если логов запуска нет, is_approved=false и попроси оркестратор запустить проверку.
- Если в коде есть опасные операции (удаление файлов, shutil.rmtree, os.remove, pathlib.unlink, rm -rf и т.п.) — is_approved=false.
- Пути к файлам указывай относительно корня проекта, как в project_structure.

ФОРМАТ ОТВЕТА — СТРОГО JSON:
{
    "is_approved": false,
    "verdict": "краткий вердикт",
    "root_cause": "почему сломалось / что не так (1-3 предложения)",
    "fix_instruction": "конкретная инструкция Editorу, что исправить",
    "dangerous_code_found": false,
    "should_create_observation": true,
    "observation_draft": {
    "rule": "короткое правило на будущее",
    "why": "почему это важно",
    "tags": ["compiler", "path", "..."]
    },
    "reasoning_summary": "как ты пришёл к выводу (2-5 предложений)"
}

Если is_approved=true:
- fix_instruction может быть пустым
- should_create_observation обычно false
- reasoning_summary всё равно обязателен"""

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