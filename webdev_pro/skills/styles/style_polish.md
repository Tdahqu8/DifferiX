# Style: Polish & Micro-Interactions

When this skill is active, your final steps MUST add the following "wow" effects to the site.

## 1. Custom Cursor
- Create a `<div class="custom-cursor"></div>` in the HTML.
- In CSS, make it a fixed element that follows the mouse (`pointer-events: none`).
- In JS, update its `left` and `top` on `mousemove`. Add a class to expand it when hovering over `a` and `button`.

## 2. Scroll Reveal
- Use `IntersectionObserver` in JS.
- Target `.feature-card`, `.hero-inner`, and section headings.
- Initially set them to `opacity: 0` and `transform: translateY(30px)`.
- When they intersect, add a `.is-visible` class that sets `opacity: 1` and `translateY(0)`.
- Use snappy CSS transitions (`transition: all 0.1s steps(2, end)` for brutalist, or `ease-out` for SaaS).

## 3. Hover States
- Cards: on hover, transform `translateY(-6px)` and add a strong box-shadow.
- Buttons: on hover, scale slightly (`transform: scale(1.05)`) or change background color.
- Links: underline on hover.

## 4. Animated Background (Optional but recommended)
- For 3D styles, ensure the Three.js canvas is present.
- For SaaS styles, add subtle floating CSS gradients.
