# Web Structure Rules

1. Filenames: `index.html`, `styles.css`, `script.js`. Never `style.css` or `main.css`.
2. Class naming: kebab-case. Use BEM-like prefixes (e.g., `.hero-title`, `.feature-card`).
3. Every class used in HTML MUST have a corresponding rule in CSS.
4. Do NOT use inline styles (`style="..."`). Move all styles to `styles.css`.
5. Keep CSS files under 150 lines per edit. If more is needed, split into multiple steps.
6. Use semantic HTML tags (`<header>`, `<main>`, `<section>`, `<footer>`).