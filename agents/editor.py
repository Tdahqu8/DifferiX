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
- "code": готовый код или XML (обязательно строкой, не объектом)

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

    # --- защита от пустого ответа ---
    if not response.choices:
        raise RuntimeError("Editor: модель вернула пустой choices")

    result_text = response.choices[0].message.content

    if result_text is None:
        raise RuntimeError(
            "Editor: модель вернула content=None. "
            "Попробуй другую модель или повтори запрос."
        )

    result_text = result_text.strip()
    if not result_text:
        raise RuntimeError("Editor: модель вернула пустую строку")

    try:
        result = json.loads(result_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Editor: невалидный JSON от модели.\nОтвет был:\n{result_text[:1000]}"
        ) from e

    # Нормализация code → всегда строка
    code = result.get("code")
    if isinstance(code, dict):
        code = (
            code.get("content")
            or code.get("code")
            or code.get("text")
            or json.dumps(code, ensure_ascii=False, indent=2)
        )
    elif isinstance(code, list):
        code = "\n".join(str(x) for x in code)
    elif code is None:
        code = ""
    else:
        code = str(code)

    result["code"] = code

    # target_file тоже строкой
    if "target_file" in result and result["target_file"] is not None:
        result["target_file"] = str(result["target_file"])

    state["generated_code"] = result
    state["current_step"] = "review"

    usage = getattr(response, "usage", None)
    usage_meta = type("Meta", (), {
        "prompt_token_count": getattr(usage, "prompt_tokens", 0) or 0,
        "candidates_token_count": getattr(usage, "completion_tokens", 0) or 0
    })

    return state, usage_meta