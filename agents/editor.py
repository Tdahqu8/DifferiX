import json
import re
from openai import OpenAI
import config

client = OpenAI(
    base_url=config.OPENROUTER_BASE_URL,
    api_key=config.OPENROUTER_API_KEY,
)

def extract_code_block(text: str) -> str:
    """Extracts raw code from Markdown ```python ... ``` blocks safely."""
    pattern = r"```(?:python|xml|html|css|json)?\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    
    # Fallback if markdown tags are at the very edges
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()

def run_editor(state, project_files=""):
    system_prompt = f"""Ты — ИИ-Редактор кода. Напиши чистый код/XML на основе плана Исследователя.

ОБЯЗАТЕЛЬНЫЙ ФОРМАТ ОТВЕТА:
Сначала укажи имя файла и действие в таком формате:
TARGET_FILE: path/to/file.py
ACTION: create (или modify)

Затем напиши весь готовый код внутри обычного markdown-блока:
```python
# твой код здесь

Список файлов проекта:
{project_files}"""

    response = client.chat.completions.create(
    model=config.MODEL_EDITOR,
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"State: {json.dumps(state, ensure_ascii=False)}"}
    ],
    temperature=0.1
)

    if not response.choices or not response.choices[0].message.content:
        raise RuntimeError("Editor: модель вернула пустой ответ")

    result_text = response.choices[0].message.content.strip()

    # Извлечение TARGET_FILE и ACTION из текстовых меток
    target_file_match = re.search(r"TARGET_FILE:\s*([^\n]+)", result_text)
    action_match = re.search(r"ACTION:\s*([^\n]+)", result_text)

    target_file = target_file_match.group(1).strip() if target_file_match else ""
    action = action_match.group(1).strip() if action_match else "modify"

    # Безопасное извлечение кода через regex
    code = extract_code_block(result_text)

    state["generated_code"] = {
        "target_file": target_file,
        "action": action,
        "code": code
    }
    state["current_step"] = "review"

    usage = getattr(response, "usage", None)
    usage_meta = type("Meta", (), {
        "prompt_token_count": getattr(usage, "prompt_tokens", 0) or 0,
        "candidates_token_count": getattr(usage, "completion_tokens", 0) or 0
    })

    return state, usage_meta