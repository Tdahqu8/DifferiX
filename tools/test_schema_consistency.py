import re
import pytest
from tools.state_manager import ExecutionControl

@pytest.mark.unit
def test_all_steps_in_main_are_valid_literals():
    # Читаем main.py и ищем все строки вида step == "something"
    with open("main.py", "r", encoding="utf-8") as f:
        main_code = f.read()
    
    used_steps = set(re.findall(r'step\s*==\s*"([^"]+)"', main_code))
    # Получаем допустимые шаги из Pydantic Literal
    allowed_steps = set(ExecutionControl.model_fields["current_step"].annotation.__args__)
    
    # Игнорируем "done" и "completed", так как они часто обрабатываются вместе
    used_steps = {s for s in used_steps if s not in ("done", "completed")}
    
    missing = used_steps - allowed_steps
    assert not missing, f"Steps used in main.py but not in ExecutionControl Literal: {missing}"