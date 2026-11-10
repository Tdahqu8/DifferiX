# Style: 3D & WebGL Volume

When this style is active, you MUST include at least one 3D or WebGL element using Three.js or CSS 3D.

## 1. Three.js Integration
- Include Three.js via CDN in the `<head>`: `<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>`.
- Create a `<canvas id="webgl-canvas">` that sits fixed in the background (`position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: -1;`).
- In `script.js`, initialize a Three.js scene:
  - Dark transparent background.
  - A rotating geometry (TorusKnot, Icosahedron, or custom particles).
  - Add `PointLight` and `AmbientLight` with colors from the CSS accent variables.
  - Handle `window.resize` to keep the canvas full-screen.
  - Use `requestAnimationFrame` to rotate the object slowly.

## 2. CSS 3D (Fallback/Enhancement)
- Use `perspective: 1000px` on container elements.
- Cards should tilt on hover using `transform: rotateX(5deg) rotateY(5deg) translateY(-5px)`.
- Add `box-shadow` with deep colored glows (e.g., `0 20px 50px var(--accent-glow)`).

## 3. Layering (Glassmorphism over 3D)
- The UI content (headers, cards) MUST sit on top of the 3D canvas.
- Use `backdrop-filter: blur(16px)` and `background: rgba(18, 22, 31, 0.6)` for the panels so the 3D background shines through slightly.