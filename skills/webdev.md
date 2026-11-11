---
id: webdev
name: Webdev
extensions: [.html]
---

# DIfferiX WebDev Skill

Rules for building and editing modern web pages. These rules are
non-negotiable. Deviating breaks the pipeline's cross-file coherence
checks and forces a cycle to be rejected.

## 1. Filename contract

These filenames are fixed:

- `index.html` — the main page (singular, lowercase)
- `styles.css` — the stylesheet (plural, lowercase)
- `script.js` — the JavaScript (singular, lowercase)

Never use any other name for these three roles. Specifically:

- Never `style.css` (singular). It will not resolve against HTML that
  references `styles.css`.
- Never `main.css`, `app.css`, `global.css`, `index.css`.
- Never `main.js`, `app.js`, `bundle.js`, `index.js`.

If additional CSS files are needed (rare), name them
`styles-<section>.css`. If additional JS files are needed, name them
`script-<feature>.js`.

Any `<link href="...">` or `<script src="...">` in the HTML must point
to a file that will exist by the end of the current task. If the
researcher's plan does not include a step for a referenced file, either
add the step or remove the reference.

## 2. Class naming contract

All classes are kebab-case and prefixed with the section they belong to.

Section prefixes:
  site-    → global chrome (site-header, site-footer, site-body)
  nav-     → navigation elements
  hero-    → hero section and its children
  features- → the features section itself
  feature- → individual feature cards
  card-    → children of a feature card
  cta-     → the call-to-action section
  form-    → form elements (inputs, labels, groups)
  footer-  → footer section (when not using site-footer)
  btn-     → button modifiers (btn-primary, btn-secondary, btn-outline)

Additional rules:
- Never use camelCase or snake_case in class names.
- Never use ID selectors for styling. IDs are for JS anchors and aria.
- Every class in HTML must have at least one matching rule in CSS.
- Every class-based selector in CSS must match a class that exists in HTML.

Canonical example:

    <section class="hero-section">
      <h1 class="hero-headline">...</h1>
      <p class="hero-description">...</p>
      <a class="btn btn-primary">...</a>
    </section>

    .hero-section { ... }
    .hero-headline { ... }
    .hero-description { ... }
    .btn { ... }
    .btn-primary { ... }

If you need a new class that does not fit an existing prefix, derive
the prefix from the enclosing section. For example, inside
`<section class="hero-section">`, prefer `hero-*` names over inventing
a new top-level prefix.

## 3. Output budget

Each edit step produces at most:

- 150 lines of CSS
- 200 lines of HTML
- 100 lines of JavaScript

If the file requires more than this, the plan must split the work into
sequential parts on the same file. Each part appends to the previous.

Do not attempt to write a 400-line CSS file in one response. The
response will be truncated mid-rule, the reviewer will reject it, and
the cycle will be wasted. Split the work.

## 4. Design tokens

All colors, spacing, radii, and transitions come from CSS custom
properties defined at the top of `styles.css`. The starting token set:

    :root {
      --bg-primary:    #0a0c10;
      --bg-secondary:  #12161f;
      --bg-tertiary:   #181d28;
      --bg-card:       #141821;
      --bg-glass:      rgba(18, 22, 31, 0.72);

      --accent:        #f59e0b;
      --accent-light:  #fbbf24;
      --accent-glow:   rgba(245, 158, 11, 0.25);
      --accent-soft:   rgba(245, 158, 11, 0.10);

      --text-main:     #f3f4f6;
      --text-muted:    #9ca3af;
      --text-dim:      #6b7280;

      --border-subtle: rgba(255, 255, 255, 0.06);
      --border-medium: rgba(255, 255, 255, 0.12);
      --border-accent: rgba(245, 158, 11, 0.35);

      --font-heading:  'Space Grotesk', system-ui, sans-serif;
      --font-body:     'Inter', system-ui, sans-serif;

      --space-sm:  0.5rem;
      --space-md:  1rem;
      --space-lg:  1.5rem;
      --space-xl:  2.5rem;
      --space-2xl: 4rem;
      --space-3xl: 6rem;

      --radius-sm:   6px;
      --radius-md:   12px;
      --radius-lg:   20px;
      --radius-full: 9999px;

      --transition-fast:   0.15s cubic-bezier(0.16, 1, 0.3, 1);
      --transition-smooth: 0.3s cubic-bezier(0.16, 1, 0.3, 1);

      --container-max: 1200px;
    }

Rules for token use:

- Never hardcode a hex color in a component rule. Use `var(--accent)`,
  not `#f59e0b`.
- Never hardcode a spacing value. Use `var(--space-lg)`, not `24px`.
- Never hardcode a radius. Use `var(--radius-md)`, not `12px`.
- Never hardcode a transition. Use `var(--transition-smooth)`, not
  `0.3s ease`.

Exception: the `:root` block itself defines values. Everywhere else
uses tokens.

## 5. Common failure modes

These are the exact patterns that break the pipeline and get rejected.
Do not do them.

**Divergent filenames.**
HTML says `<link href="style.css">`, CSS file is `styles.css`. The
coherence checker flags it. Fix: use §1 filenames exactly.

**Untargeted CSS.**
CSS defines `.navbar` but HTML uses `.site-header`. The coherence
checker reports `.navbar` as orphan and `.site-header` as unstyled.
Fix: every selector must have an HTML counterpart.

**Missing script file.**
HTML references `<script src="script.js">`, no script.js is created.
Fix: if the HTML references a script, the plan must include a step
for it. If no script is needed, remove the `<script>` tag.

**Truncated CSS.**
The stylesheet ends mid-rule: `.hero-headline { font-size: 3rem;`
The reviewer will reject with "unclosed rule". Fix: stay within the
budget in §3, split into parts.

**Hardcoded values.**
`color: #f59e0b` instead of `color: var(--accent)`. Fix: use the token.

**`!important`.**
Any use of `!important` is a signal that the selector hierarchy is
wrong. Fix: restructure the CSS.

**Animations without `prefers-reduced-motion` guard.**
Any GSAP or CSS animation must be wrapped:

    if (window.matchMedia('(prefers-reduced-motion: no-preference)').matches) {
      // animation code
    }

**Empty structural `<div>`s.**
`<div class="hero-visual"></div>` with no CSS rule targeting
`.hero-visual` renders as nothing (or worse, as a stray box). Fix:
either style it or remove it.

## 6. Accessibility floor

Every page produced must have:

- `<html lang="en">` (or the correct language)
- `<meta name="viewport" content="width=device-width, initial-scale=1.0">`
- Semantic tags: `<header>`, `<main>`, `<section>`, `<footer>`, `<nav>`
- `alt` attribute on every `<img>` (empty `alt=""` for decorative)
- `aria-label` on icon-only interactive elements
- `:focus-visible` styles on buttons, links, and inputs
- `prefers-reduced-motion` guard on animations

## 7. When the pipeline rejects

Read the rejection reason exactly. It names the specific failure.

- If the reason names missing files: create the missing files. Do not
  invent new names; use the exact names referenced in the HTML.
- If the reason names unstyled classes: add CSS rules for exactly those
  classes. Do not rewrite unrelated rules.
- If the reason says "truncated": split the remaining work into smaller
  edits. Do not try to fit more content into one response.
- If the reason is a coherence report: the report lists the specific
  HTML classes without matching CSS rules and the specific CSS rules
  without matching HTML classes. Fix only what is named.

Do not rewrite an entire file to fix one missing rule. Use SEARCH/REPLACE
on the specific location.