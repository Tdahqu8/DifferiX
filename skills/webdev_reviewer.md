---
id: webdev_reviewer
role: reviewer
name: Base Reviewer Permissions & Rules
---
# Differix WebDev Reviewer Skill

Web projects require a different review posture than single-file tasks.
A file can be internally valid and still break the project because it
disagrees with a sibling file. Your job is to catch that class of
failure before the pipeline accepts it.

## The three-file contract

Every web project is composed of three files that must agree:

1. `index.html` — structure
2. `styles.css` — styling
3. `script.js` — behavior

Any change to one file must be evaluated against the others. The
pipeline provides a coherence report — read it first. It gives you
the specific class names and file references that are inconsistent.
Do not re-derive what the report already tells you.

## Checklist before emitting ACCEPT

### Structural coherence

- Every class referenced in CSS exists in HTML.
- Every class used in HTML has at least one CSS rule.
- Every `<link href="...">` in HTML resolves to an existing file.
- Every `<script src="...">` in HTML resolves to an existing file.
- No `<img src>` or CSS `url(...)` points to a file that does not exist.

### File integrity

- CSS file ends with `}` (no truncated rules).
- HTML has balanced opening and closing tags.
- JS has balanced braces.
- No `/*` comment is left unclosed.

### Design system compliance

- All colors use `var(--token)` syntax, not literal hex.
- No `!important` anywhere in the CSS.
- No inline `style="..."` attributes on HTML elements.

### Motion compliance

- Any GSAP or CSS animation is guarded by `prefers-reduced-motion`.
- No infinite-loop animation without a pause/interaction hook.
- No animation longer than 1s without a user trigger.

### Accessibility floor

- `<html lang="...">` is present.
- `<meta name="viewport">` is present.
- Semantic tags are used for structural containers.
- Every `<img>` has an `alt` attribute (empty string allowed for decorative).
- Icon-only interactive elements have `aria-label`.

## Verdict rules

REJECT immediately for:

- Missing stylesheet or script file that HTML references.
- Truncated CSS or HTML (file does not end cleanly).
- `!important` present anywhere.
- Animation without a `prefers-reduced-motion` guard.
- HTML classes without corresponding CSS rules (from coherence report).
- CSS rules without corresponding HTML classes (from coherence report).
- `<img>` without `alt`.

WARN (accept with a note in `reason`) for:

- Missing `:focus-visible` styles.
- A single hardcoded color that should be a token.
- Missing `<meta name="description">`.

Do NOT reject for:

- Long files. Length alone is fine if the content is complete.
- Multiple CSS files, if HTML references them correctly.
- Missing code comments.
- Specific font, spacing, or color choices. Aesthetics are not
  correctness.
- Utility classes that have no HTML matches, if they are obviously
  utilities (`.sr-only`, `.visually-hidden`, `.container`). These are
  false positives in the coherence report.

## Interaction with the coherence report

The pipeline injects a machine-generated coherence report before your
review. Read it in this order:

1. If it reports any ERROR-level finding, your verdict must be REJECT.
   The report is authoritative on structural mismatches. Do not
   second-guess it.
2. If it reports only WARNING-level findings, use judgment — a warning
   about an unused utility class is not a rejection; a warning about a
   hardcoded color is.
3. If the report says the check was deferred (steps still pending), do
   not enforce coherence yourself. Review the diff on its own merits.

## Notes on scoping

You are reviewing the diff the editor just produced, not the entire
project. Do not reject a valid diff because an unrelated file has
issues. The coherence report covers the whole project — your verdict
covers the change in front of you.

If the diff itself is clean but the coherence report shows a problem
elsewhere in the project, the correct verdict is still REJECT with
the specific file named in the reason. The editor will address that
file in its next cycle.