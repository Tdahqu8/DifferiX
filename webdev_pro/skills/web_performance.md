# Skill: Web Performance & Core Web Vitals

When building a website, you MUST implement the following performance optimizations to ensure a perfect Lighthouse score.

## 1. Images & Media
- ALWAYS add `loading="lazy"` and `decoding="async"` to all `<img>` tags below the fold.
- ALWAYS specify `width` and `height` attributes on `<img>` to prevent Layout Shift (CLS).
- If using background images in CSS, ensure they do not block rendering.
- Recommend modern formats (WebP, AVIF) in `<picture>` tags if possible.

## 2. Fonts & CSS
- Add `<link rel="preconnect" href="https://fonts.googleapis.com">` and `<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>` before font links.
- Use `font-display: swap;` in CSS for all custom web fonts to prevent invisible text (FOIT).
- Keep CSS minimal. Avoid deeply nested selectors. Do not use `@import` inside CSS files (it blocks rendering). 

## 3. JavaScript
- ALWAYS add `defer` to `<script>` tags in the `<head>` so they don't block HTML parsing.
- If a script must run early, use `async`.
- Use `IntersectionObserver` instead of `scroll` event listeners for scroll-based animations to prevent main-thread blocking.

## 4. Animations & GPU Acceleration
- NEVER animate `width`, `height`, `top`, `left`, or `margin`. These trigger Layout thrashing.
- ALWAYS animate `transform` (translate, scale, rotate) and `opacity`. These are composited by the GPU.
- Add `will-change: transform;` to elements that will animate heavily (like custom cursors or parallax backgrounds).

## 5. DOM Structure
- Keep the DOM tree shallow. Do not wrap single elements in multiple unnecessary `<div>`s.