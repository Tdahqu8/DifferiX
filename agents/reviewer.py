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

    # 1. Обрезаем context, чтобы не перегружать окно модели лишним мусором
    compiler_log = str(state.get("compiler_output", ""))
    pruned_state = {
        "iteration_count": state.get("iteration_count", 0),
        "compiler_output": compiler_log[-3000:] if len(compiler_log) > 3000 else compiler_log,  # берем хвост лога
        "generated_code": state.get("generated_code"),
        "research_data": state.get("research_data"),
        "warnings": state.get("warnings", [])
    }

    # 2. Вызов API с гарантированным запасом токенов
    response = client.chat.completions.create(
        model=config.MODEL_REVIEWER,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"State: {json.dumps(pruned_state, ensure_ascii=False)}"}
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=2048
    )
    
    # 3. Валидация ответа API
    if not response or not getattr(response, "choices", None) or len(response.choices) == 0:
        raise RuntimeError("Reviewer: модель вернула пустой ответ или response.choices == None")

    first_choice = response.choices[0]
    if not first_choice or not getattr(first_choice, "message", None):
        raise RuntimeError("Reviewer: отсутствует message в ответе модели")

    result_text = (first_choice.message.content or "").strip()
    if not result_text:
        raise RuntimeError("Reviewer: модель вернула пустую строку content")

    # 4. Безопасный парсинг JSON с восстановлением (json_repair) и фоллбеком
    result = None
    try:
        result = json.loads(result_text)
    except json.JSONDecodeError:
        try:
            import json_repair
            result = json_repair.loads(result_text)
            print("⚠️ [Reviewer]: Обнаружен невалидный JSON, успешно восстановлен через json_repair.")
        except Exception:
            print("⚠️ [Reviewer]: Модель обрезала ответ. Выполняем мягкий откат на шаг редактирования.")
            result = {
                "is_approved": False,
                "verdict": "Ошибка формата ответа Ревизора (JSON был обрезан)",
                "root_cause": "Модель превысила лимит генерации или оборвала ответ на середине.",
                "fix_instruction": "Произошла ошибка парсинга ответа проверки. Повтори попытку или проверь синтаксис файла."
            }

    # 5. Обработка вердикта и маршрутизация состояния
    if not result.get("is_approved", False):
        fix_instruction = result.get("fix_instruction", "")

        if isinstance(state.get("research_data"), dict):
            state["research_data"]["reviewer_fix"] = fix_instruction
        elif isinstance(state.get("research_data"), str):
            state["research_data"] += f"\n[Правка Ревизора]: {fix_instruction}"
        else:
            state["research_data"] = f"[Правка Ревизора]: {fix_instruction}"

        state["review_result"] = result
        state["current_step"] = "edit"
        state["iteration_count"] = state.get("iteration_count", 0) + 1
    else:
        state["review_result"] = result
        state["current_step"] = "done"
        
    # 6. Извлечение токенов
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
    completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0

    usage_meta = type('Meta', (), {
        'prompt_token_count': prompt_tokens,
        'candidates_token_count': completion_tokens
    })
    
    return state, usage_meta