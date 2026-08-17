import os
import json
from openai import OpenAI
import config

client = OpenAI(
    base_url=config.OPENROUTER_BASE_URL,
    api_key=config.OPENROUTER_API_KEY,
)

def load_lessons(project_name):
    if not os.path.exists("lessons_learned.json"): 
        return ""
    with open("lessons_learned.json", "r", encoding="utf-8") as f:
        lessons = json.load(f)
    rules = [f"- RULE: {l['rule_to_prevent']}" for l in lessons if project_name in l['context']]
    return "\nPAST ERRORS AND LESSONS LEARNED:\n" + "\n".join(rules) if rules else ""

def run_researcher(state, project_name, project_files=""):
    lessons_context = load_lessons(project_name)
    
    system_prompt = f"""You are the AI Researcher. Your goal is to study the task from state.json and formulate a technical plan for the Editor.
Use only verified API methods. Do not invent syntax.
{lessons_context}

Project files:
{project_files}

Output STRICTLY JSON with keys "analysis" and "technical_plan"."""

    response = client.chat.completions.create(
        model=config.MODEL_RESEARCHER,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Current state: {json.dumps(state, ensure_ascii=False)}"}
        ],
        response_format={"type": "json_object"},
        temperature=0.1
    )
    
    result_text = response.choices[0].message.content
    result = json.loads(result_text)
    
    state["research_data"] = result["technical_plan"]
    state["current_step"] = "edit"
    
    usage_meta = type('Meta', (), {
        'prompt_token_count': response.usage.prompt_tokens,
        'candidates_token_count': response.usage.completion_tokens
    })
    
    return state, usage_meta