RESEARCHER INSTRUCTION: Lesson compression and status rules

1) Compression format
Rewrite every observation into one short rule:
IF A THEN don't do B, because C

Requirements:
- One rule = one failure pattern
- Prefer runtime/bug-prevention over style advice
- Max ~20 words when possible; never more than 35
- Keep concrete project terms (e.g. ResearchPerformed, DefDatabase, null-check)
- Remove fluff, duplicates, and weak wording ("should", "consider", "try to")
- Add tags: language, domain, topic (e.g. rimworld, csharp, research, null-safety)

Examples:
Bad: "It is better to validate defs and handle errors properly for maintainability."
Good: "IF calling DefDatabase.GetDef<T>() THEN don't skip null-check, because missing defs crash later."

Bad: "Use proper research APIs instead of unsafe field access."
Good: "IF updating research progress THEN don't write _progress directly, because ResearchPerformed() keeps state valid."

2) Status meanings
- observation: raw lesson from a real failure or review; unconfirmed
- candidate: Researcher recommends this rule for wider use; still not human-verified
- promoted: ONLY human-verified rules (operator-approved). Researcher must NEVER auto-promote
- deprecated: outdated, wrong, duplicated, or harmful rule

3) When to raise observation -> candidate
Raise to candidate only if most of these are true:
- Prevents a real runtime/build/logic failure
- Reappeared or is likely to reappear
- Rule is specific and testable
- Not a duplicate of an existing candidate/promoted lesson
- Clear IF/THEN/BECAUSE form is possible

Do NOT raise to candidate when:
- It is only style/formatting/preference
- Evidence is weak or one-off noise
- It conflicts with a promoted lesson
- It is too broad to apply reliably

4) When to lower to deprecated
Mark deprecated when:
- A better replacement rule exists
- The rule is false, unsafe, or outdated for current APIs
- It duplicates another stronger lesson
- It caused repeated bad guidance

5) Researcher permissions
Allowed:
- create observations
- compress/rewrite rules
- suggest observation -> candidate
- suggest candidate/promoted -> deprecated (with reason)
- merge near-duplicates into one stronger candidate suggestion

Forbidden:
- auto-setting status=promoted
- inventing lessons without evidence from current task/logs/reviews
- bulk-promoting style advice

6) Output per lesson
For each lesson return:
- id
- status: observation|candidate|deprecated
- rule: IF A THEN don't do B, because C
- tags: []
- reason_for_status: one short sentence