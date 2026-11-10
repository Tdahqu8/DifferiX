import json
import math
import os
import re
import uuid
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator


class BaseStateModel(BaseModel):
    """Базовая модель с поддержкой сохранения произвольных доп. полей от LLM."""
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class MetaModel(BaseStateModel):
    version: str = "1.1"
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    target_hardware: str = "generic"
    language: str = "auto"


class ExecutionControl(BaseStateModel):
    status: Literal["idle", "planning", "in_progress", "paused", "done", "failed", "completed", "reflect"] = (
        "idle"
    )
    current_step: Literal["init", "research", "edit", "review", "done", "idle", "reflect"] = "init"
    active_agent: str = "researcher"
    iteration_count: int = 0
    max_iterations: int = 10
    edit_retries: int = 0
    max_edit_retries: int = 3


class Environment(BaseStateModel):
    build_command: str = ""
    test_command: str = ""
    target_files: List[str] = Field(default_factory=list)
    active_target_file: Optional[str] = None


class TaskSpec(BaseStateModel):
    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:6]}")
    raw_user_prompt: str = ""
    decomposed_goal: str = ""
    domain: str = ""
    doc_constraints: List[str] = Field(default_factory=list)


class PlanStep(BaseStateModel):
    """
    Универсальный шаг плана.

    Поля, связанные с cross-file координацией, добавлены для борьбы с
    рассинхронизацией HTML/CSS/JS и больших файлов:
      - estimated_lines: сколько строк кода примерно выдаст этот шаг
      - provides:        список новых идентификаторов (классов, функций, файлов)
      - consumes:        список идентификаторов из предыдущих шагов
      - part:            маркер части (например, "2/3") если шаг разбит
      - depends_on:      id шагов, которые должны завершиться раньше
    """
    id: Union[int, str] = Field(default=1)
    title: str
    description: str = ""
    target_file: Optional[str] = None
    assign_to: str = "editor"
    status: Literal["pending", "in_progress", "completed", "failed"] = "pending"
    estimated_lines: int = 0
    provides: List[str] = Field(default_factory=list)
    consumes: List[str] = Field(default_factory=list)
    part: Optional[str] = None
    depends_on: List[Union[int, str]] = Field(default_factory=list)

    @field_validator("id", mode="before")
    @classmethod
    def coerce_id(cls, v: Any) -> Union[int, str]:
        if v is None:
            return 1
        return v


class FeedbackLoop(BaseStateModel):
    human_feedback: Optional[str] = None
    reviewer_feedback: Optional[str] = None
    last_error: Optional[str] = None


class Metrics(BaseStateModel):
    total_tokens: int = 0
    total_cost_usd: float = 0.0


class AppState(BaseStateModel):
    schema_version: str = "2.0"
    meta: MetaModel = Field(default_factory=MetaModel)
    execution_control: ExecutionControl = Field(default_factory=ExecutionControl)
    environment: Environment = Field(default_factory=Environment)
    system_constraints: List[str] = Field(default_factory=list)
    task_spec: TaskSpec = Field(default_factory=TaskSpec)
    plan: List[PlanStep] = Field(default_factory=list)
    feedback_loop: FeedbackLoop = Field(default_factory=FeedbackLoop)
    metrics: Metrics = Field(default_factory=Metrics)
    findings_and_context: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Plan chunking
# ---------------------------------------------------------------------------
_DEFAULT_CHUNK_THRESHOLD = 200


def _chunk_threshold() -> int:
    try:
        import config
        return int(getattr(config, "PLAN_CHUNK_THRESHOLD", _DEFAULT_CHUNK_THRESHOLD))
    except Exception:
        return _DEFAULT_CHUNK_THRESHOLD


def _maybe_chunk_step(step: PlanStep) -> List[PlanStep]:
    """
    Split a PlanStep into sequential parts if its estimated output exceeds
    the chunk threshold. Each part is a separate PlanStep targeting the same
    file, with a "part N/M" title marker.

    No-op if estimated_lines is unset or below threshold.
    """
    threshold = _chunk_threshold()
    if not step.estimated_lines or step.estimated_lines <= threshold:
        return [step]

    n_parts = math.ceil(step.estimated_lines / threshold)
    parts: List[PlanStep] = []
    for i in range(n_parts):
        remaining = step.estimated_lines - i * threshold
        chunk_lines = min(threshold, remaining)
        part_title = f"{step.title} (part {i + 1}/{n_parts})"
        p = PlanStep(
            title=part_title,
            description=step.description,
            target_file=step.target_file,
            assign_to=step.assign_to,
            status=step.status,
            estimated_lines=chunk_lines,
            provides=list(step.provides),
            consumes=list(step.consumes),
            part=f"{i + 1}/{n_parts}",
            depends_on=list(step.depends_on),
        )
        parts.append(p)

    # Chain: part 2 depends on part 1, etc.
    for i in range(1, len(parts)):
        parts[i].depends_on.append(parts[i - 1].id if parts[i - 1].id else i)
    return parts


# ---------------------------------------------------------------------------
# State template
# ---------------------------------------------------------------------------
def build_state_template(
    user_prompt: str = "",
    target_hardware: str = "generic",
    system_constraints: Optional[List[str]] = None,
) -> AppState:
    is_active = bool(user_prompt and user_prompt.strip())

    return AppState(
        meta=MetaModel(target_hardware=target_hardware),
        execution_control=ExecutionControl(
            status="planning" if is_active else "idle",
            current_step="research" if is_active else "init",
            active_agent="researcher",
        ),
        system_constraints=system_constraints or [],
        task_spec=TaskSpec(raw_user_prompt=user_prompt),
    )


def load_state(filepath: str) -> AppState:
    if not os.path.exists(filepath):
        return build_state_template()

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        return AppState.model_validate(raw_data)
    except Exception as e:
        print(f"[StateManager Warning] Failed to parse state.json ({e}).")

        backup_path = f"{filepath}.corrupted_{int(datetime.now().timestamp())}"
        try:
            if os.path.exists(filepath):
                os.rename(filepath, backup_path)
                print(f"[StateManager] Backup of broken state saved to: {backup_path}")
        except OSError as err:
            print(f"[StateManager Warning] Could not rename broken state file: {err}")

        print("[StateManager] Rebuilding clean template.")
        return build_state_template()


def save_state(state: Union[AppState, dict], filepath: str) -> None:
    if isinstance(state, dict):
        if "execution_control" not in state or not isinstance(
            state["execution_control"], dict
        ):
            state["execution_control"] = {}
        if "task_spec" not in state or not isinstance(state["task_spec"], dict):
            state["task_spec"] = {}

        if "current_step" in state:
            state["execution_control"]["current_step"] = state["current_step"]
        if "status" in state:
            state["execution_control"]["status"] = state["status"]
        if "active_agent" in state:
            state["execution_control"]["active_agent"] = state["active_agent"]
        if "iteration_count" in state:
            state["execution_control"]["iteration_count"] = state["iteration_count"]

        if "task_prompt" in state:
            state["task_spec"]["raw_user_prompt"] = state["task_prompt"]
        if "goal" in state and not state["task_spec"].get("raw_user_prompt"):
            state["task_spec"]["raw_user_prompt"] = state["goal"]

        app_state = AppState.model_validate(state)
    else:
        app_state = state

    target_dir = os.path.dirname(os.path.abspath(filepath))
    os.makedirs(target_dir, exist_ok=True)

    temp_file = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=target_dir, delete=False, encoding="utf-8"
        ) as tf:
            tf.write(app_state.model_dump_json(indent=2))
            temp_file = tf.name

        os.replace(temp_file, filepath)
    except Exception as e:
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)
        raise e


def extract_json_payload(raw_text: str) -> Union[dict, list]:
    if not isinstance(raw_text, str):
        return raw_text
    match = re.search(r"```(?:json)?\s*([\[\{].*?[\]\}])\s*```", raw_text, re.DOTALL)
    candidate = match.group(1) if match else raw_text.strip()
    start_obj, start_arr = candidate.find("{"), candidate.find("[")
    if start_arr != -1 and (start_obj == -1 or start_arr < start_obj):
        end = candidate.rfind("]")
        if end != -1:
            candidate = candidate[start_arr:end + 1]
    elif start_obj != -1:
        end = candidate.rfind("}")
        if end != -1:
            candidate = candidate[start_obj:end + 1]
    return json.loads(candidate)


def apply_researcher_plan(
    current_state: AppState, researcher_data: Union[dict, list, str]
) -> AppState:
    """
    Мерджит вывод LLM-Исследователя в AppState.

    New in this version:
      - Preserves estimated_lines / provides / consumes from the researcher
      - Chunks steps whose estimated_lines exceed the threshold
      - Preserves completed work by merging on completed-status boundary
    """
    raw_plan = None

    if isinstance(researcher_data, str):
        try:
            researcher_data = extract_json_payload(researcher_data)
        except Exception:
            pass

    if isinstance(researcher_data, list):
        raw_plan = researcher_data
    elif isinstance(researcher_data, dict):
        if researcher_data.get("decomposed_goal"):
            current_state.task_spec.decomposed_goal = str(
                researcher_data["decomposed_goal"]
            )
        elif researcher_data.get("analysis") and not current_state.task_spec.decomposed_goal:
            current_state.task_spec.decomposed_goal = str(researcher_data["analysis"])[:500]

        if isinstance(researcher_data.get("findings_and_context"), dict):
            current_state.findings_and_context = researcher_data["findings_and_context"]

        for key in ("technical_plan", "plan", "steps", "raw_response"):
            candidate = researcher_data.get(key)
            if isinstance(candidate, list) and candidate:
                raw_plan = candidate
                break

    if isinstance(raw_plan, list) and raw_plan:
        default_target = (
            current_state.environment.active_target_file
            or (researcher_data.get("target_file") if isinstance(researcher_data, dict) else None)
        )
        valid_status = {"pending", "in_progress", "completed", "failed"}

        completed = [s for s in current_state.plan if s.status == "completed"]
        new_steps: List[PlanStep] = []

        for item in raw_plan:
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("description") or "").strip()
                if not title:
                    continue

                status = item.get("status", "pending")
                try:
                    est_lines = int(item.get("estimated_lines", 0) or 0)
                except (TypeError, ValueError):
                    est_lines = 0

                provides = [str(p) for p in (item.get("provides") or []) if isinstance(p, (str, int))]
                consumes = [str(p) for p in (item.get("consumes") or []) if isinstance(p, (str, int))]

                step = PlanStep(
                    title=title,
                    description=str(item.get("description", "")),
                    target_file=item.get("target_file") or default_target,
                    assign_to=str(item.get("assign_to", "editor")),
                    status=status if status in valid_status else "pending",
                    estimated_lines=est_lines,
                    provides=provides,
                    consumes=consumes,
                )
                new_steps.extend(_maybe_chunk_step(step))

            elif isinstance(item, str) and item.strip():
                step = PlanStep(
                    title=item.strip(),
                    description=item.strip(),
                    target_file=default_target,
                )
                new_steps.append(step)

        if new_steps:
            merged = completed + new_steps
            for idx, step in enumerate(merged, start=1):
                step.id = idx
            current_state.plan = merged

    has_pending = any(s.status == "pending" for s in current_state.plan)
    if has_pending:
        current_state.execution_control.status = "in_progress"
        current_state.execution_control.current_step = "edit"
        current_state.execution_control.active_agent = "editor"
    else:
        current_state.execution_control.status = "planning"
        current_state.execution_control.current_step = "research"
        current_state.execution_control.active_agent = "researcher"

    return current_state


__all__ = [
    "AppState",
    "ExecutionControl",
    "Environment",
    "FeedbackLoop",
    "MetaModel",
    "Metrics",
    "PlanStep",
    "TaskSpec",
    "apply_researcher_plan",
    "build_state_template",
    "extract_json_payload",
    "load_state",
    "save_state",
]