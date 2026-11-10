# Web Design Rules

1. ALWAYS use CSS variables for colors (`var(--bg-primary)`, `var(--text-main)`, `var(--accent)`).
2. NEVER use pure black (#000) or pure white (#fff). Use tinted grays (e.g., #0a0c10).
3. Use `clamp()` for typography sizing to ensure responsiveness (e.g., `font-size: clamp(2rem, 5vw, 4rem)`).
4. Use modern layout tools: CSS Grid for 2D layouts, Flexbox for 1D alignment.
5. For glassmorphism effects, use `backdrop-filter: blur(12px)` combined with an rgba background.
6. Add smooth transitions for hover states (e.g., `transition: transform 0.2s ease`).
7. Ensure sufficient contrast ratio (at least 4.5:1 for body text).ц