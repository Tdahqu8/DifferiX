---
id: cta-btn
category: button
tags: [btn, button, cta]
source: cta-btn.html
classes: [cta-btn]
---

# cta-btn

## HTML

```html
<button onclick="funcPlaceholder()" class="cta-btn">Do something</button>

    <script>
        function funcPlaceholder() {
            // here you must place cta's function
        }
    </script>
```

## CSS

```css
.cta-btn {
  background: var(--clr-primary);
            color: var(--clr-white);
            font-family: var(--ff-label);
            font-size: 1.4rem;
            padding: 16px 40px;
            border: 2px solid var(--clr-primary);
            border-radius: 999px;
            cursor: pointer;
            white-space: nowrap;
            font-weight: bold;
            box-shadow: var(--shadow-soft);
            transition: all 0.2s;
}

.cta-btn:hover {
  transform: translateY(-3px);
            box-shadow: 0 12px 40px rgba(255, 216, 150, 0.5);
}
```

## JavaScript

```javascript
function funcPlaceholder() {
            // here you must place cta's function
        }
```
