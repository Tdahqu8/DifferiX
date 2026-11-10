---
id: result-box
category: data
tags: [box, data, label, output, result, row]
source: result-box.html
classes: [result-label, result-output, result-row]
---

# result-box

## HTML

```html
<div class="result-row">
        <div class="result-label">Something</div>
        <div class="result-output" id="result">Output placeholder</div>
    </div>
```

## CSS

```css
.result-row {
  display: flex;
            align-items: center;
            margin-bottom: 14px;
            height: 48px;
            border: 2px solid var(--clr-primary);
            border-radius: 24px;
            overflow: hidden;
}

.result-label {
  background-color: var(--clr-primary);
            color: var(--clr-dark);
            font-family: var(--ff-label);
            font-size: 1.05rem;
            width: 118px;
            height: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            font-weight: 500;
}

.result-output {
  padding: 0 20px;
            font-family: var(--ff-body);
            font-size: 1.1rem;
            color: var(--clr-dark);
            display: flex;
            align-items: center;
            width: 100%;
            height: 100%;
            background-color: var(--clr-white);
            user-select: all;
            font-weight: 500;
}
```
