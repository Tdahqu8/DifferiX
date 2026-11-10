<div align="center">
  <h1>DifferiX</h1>
  <p><strong>The Deterministic, Local-First OS for Autonomous AI Agents.</strong></p>
  <p>
    <a href="#features">Features</a> •
    <a href="#quick-start">Quick Start</a> •
    <a href="#architecture">Architecture</a> •
    <a href="#plugins">Plugins</a>
  </p>
</div>

---

DifferiX is an open, model-agnostic multi-agent runtime. It doesn't just let LLMs write code; it forces them to write **verified, executable code** inside a deterministic Python shell.

Most "autonomous agents" fail because they hallucinate success and loop endlessly. DifferiX solves this with **Zero Trust Architecture**, **Tiered Context Assembly**, and a **Local Experience Engine**.

## 🧠 Core Architectural Patterns

1. **Zero Trust to Agent:** A stochastic LLM is never trusted with state mutation. Success is verified by compilers, ASTs, and fs-hashes, not by the agent's words.
2. **Probabilistic in a Deterministic Shell:** The LLM acts purely as a translator. Python scripts act as the supreme judge, sandbox, and state machine.
3. **Scarcity by Design (TCA):** Artificial token budgets force the LLM to focus on surgical context (reducing input tokens by 80%).
4. **Evolutionary Memory (A-Score):** Experience is stored as abstract math. Unhelpful lessons fade out; helpful ones propagate without retraining the LLM.

## ✨ Features

*   **Verification Firewall:** Blocks agents from faking success. If the file on disk didn't change, the patch is rejected.
*   **Behavior Guard:** Detects prompt-context bloat and repeated actions in real-time. Kills the loop before it burns your API budget.
*   **Tiered Context Assembly (TCA):** Waterfall token budgeting. AST-slicing for Python, Class Inventory extraction for CSS.
*   **Dynamic Skill Injection:** The Researcher analyzes the task and injects *only* the relevant skills (e.g., `style_brutalist` or `style_3d_webgl`).
*   **Recursive Self-Improvement (RSI):** The framework can safely modify its own source code, protected by a critical-file gate.
*   **Model Agnostic:** Works with OpenRouter, Anthropic, Google, or local Ollama models.

## 🚀 Quick Start

```bash
# Clone the repository
git clone https://github.com/tdahq8/differix.git
cd differix

# Setup environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Add your API key
cp .env.example .env
# Edit .env and add OPENROUTER_API_KEY=sk-or-v1-...

# Run DifferiX
export VEGEX_RSI_MODE=1
python main.py

🎨 Showcase (WebDev Pro Plugin)

DifferiX dynamically injects design skills to generate production-ready UIs.

(Add the screenshot of the brutalist Web3 site here)

    "Create a portfolio landing page for a Web3 graffiti artist. Make it look raw and bold." — Generated in 4 cycles using the style_brutalist skill.

🏗️ Architecture
text

[ User ] <--> [ LLM Interpreter ]
                     |
      +--------------+--------------+
      |                             |
[ Symbolic Verifier ]       [ Experience Engine ]
(Python / AST / Compilers)  (A-Score / SQLite / Math)
      |                             |
      +--------------+--------------+
                     |
             [ Verified Code ]

🔌 Ecosystem & Plugins

DifferiX supports .differixplugin files. Install plugins to give agents new superpowers (WebDev, EdTech, Crypto Auditing).

Note: Executable code is strictly forbidden inside plugins to prevent RCE. Plugins are data-only (.md, .json, .css).
📜 License

MIT License. Build, fork, and create.