// ═══════════════════════════════════════════════════════════════════════════════
//  CalCook — Fuzzy Search
//  Handles the nav search bar: live suggestions with typo tolerance.
//
//  HOW TO ADD A NEW TOOL:
//    Just push another object into the TOOLS array below.
//    The search engine reads title, description, and tags automatically.
// ═══════════════════════════════════════════════════════════════════════════════

// ─── 1. TOOLS DATABASE ────────────────────────────────────────────────────────
//  Each entry needs:
//    title       — shown in the dropdown
//    description — shown as a subtitle
//    url         — where clicking takes the user
//    icon        — emoji shown left of the result
//    tags        — extra keywords (synonyms, common misspellings, related terms)

const TOOLS = [
  {
    title:       "Recipe Scanner",
    description: "Scan handwritten or printed recipes with AI",
    url:         "scanner/scanner.html",
    icon:        "📷",
    tags:        ["scan", "ai", "photo", "image", "handwritten", "ocr",
                  "camera", "upload", "picture", "read"],
  },
  {
    title:       "Unit Converter",
    description: "Convert cups, grams, ml, tbsp, oz and more",
    url:         "converter/converter.html",
    icon:        "⇄",
    tags:        ["convert", "units", "cups", "grams", "ml", "tbsp", "tsp",
                  "ounce", "oz", "liter", "pound", "measurement", "scales"],
  },
  {
    title:       "Baking Ratio Calculator",
    description: "Calculate ingredient ratios for bread, pastry and more",
    url:         "ratio/ratio.html",
    icon:        "⚖️",
    tags:        ["ratio", "baking", "bread", "pastry", "dough", "flour",
                  "water", "sourdough", "pizza", "calculate", "proportion"],
  },
  {
    title:       "Pan Size Converter",
    description: "Convert quantities between different baking pan sizes",
    url:         "pan-size/pan-size.html",
    icon:        "🔲",
    tags:        ["pan", "tin", "dish", "size", "round", "square", "loaf",
                  "springform", "bake", "area", "volume"],
  },
  {
    title:       "Recipe Scaler",
    description: "Scale any recipe up or down by servings",
    url:         "scaler/scaler.html",
    icon:        "📐",
    tags:        ["scale", "servings", "double", "halve", "triple", "portions",
                  "multiply", "adjust", "resize", "people"],
  },
  {
    title:       "Recipe cost calculator",
    description: "Scale any recipe up or down by servings",
    url:         "cost/cost.html",
    icon:        "💵",
    tags:        ["cost", "recipe", "calculator", "money", "how much will cost",
                  "how much", "will cost", "price", "much"],
  },
];


// ─── 2. FUZZY MATCHING ALGORITHM ──────────────────────────────────────────────

/**
 * Levenshtein distance — the minimum number of single-character edits
 * (insert, delete, replace) needed to turn string `a` into string `b`.
 *
 * Example:  levenshtein("dcales", "scales") → 2
 *           levenshtein("calcilator", "calculator") → 1
 */
function levenshtein(a, b) {
  if (a === b) return 0;
  if (a.length === 0) return b.length;
  if (b.length === 0) return a.length;

  // Space-optimised: keep only two rows instead of a full matrix
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  let curr = new Array(b.length + 1);

  for (let i = 1; i <= a.length; i++) {
    curr[0] = i;
    for (let j = 1; j <= b.length; j++) {
      curr[j] = a[i - 1] === b[j - 1]
        ? prev[j - 1]                                         // characters match → free
        : 1 + Math.min(prev[j], curr[j - 1], prev[j - 1]);  // delete / insert / replace
    }
    [prev, curr] = [curr, prev]; // swap rows for next iteration
  }
  return prev[b.length];
}

/**
 * How similar is one word to another?
 * Returns 0 (perfect match) → 1 (completely different).
 *
 * A substring match always scores 0 so that "tbsp" inside "tablespoon" is free.
 * Otherwise we normalise Levenshtein by the longer word's length.
 */
function wordSimilarity(queryWord, targetWord) {
  if (targetWord.includes(queryWord)) return 0; // substring → perfect

  const dist = levenshtein(queryWord, targetWord);
  return dist / Math.max(queryWord.length, targetWord.length);
}

/**
 * Score one tool against the full query string.
 * Lower score = better match.  Infinity = no match at all.
 *
 * Strategy:
 *   1. Split the query into individual words.
 *   2. Build a "corpus" from the tool's title + description + tags.
 *   3. For each query word find the closest corpus word.
 *   4. Average those per-word scores.
 *
 * This is why "dcales calcilator" matches "Unit Converter" or "Recipe Scaler":
 *   "dcales"    → closest corpus word "scales"     → score ~0.17 (1 edit in 6)
 *   "calcilator"→ closest corpus word "calculator" → score ~0.10 (1 edit in 10)
 *   average ≈ 0.14 → well below the 0.45 threshold → result shown ✓
 */
function scoreTool(tool, query) {
  const queryWords = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  if (queryWords.length === 0) return Infinity;

  // Flatten everything the tool knows about itself into one list of words
  const corpus = [tool.title, tool.description, ...tool.tags]
    .join(' ')
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);

  // Average best-match score across all query words
  let total = 0;
  for (const qw of queryWords) {
    let best = Infinity;
    for (const cw of corpus) {
      const s = wordSimilarity(qw, cw);
      if (s < best) best = s;
      if (s === 0) break; // can't do better than perfect
    }
    total += best;
  }
  return total / queryWords.length;
}

/**
 * Run a fuzzy search and return tools sorted best → worst.
 *
 * `threshold` controls how tolerant the search is.
 *   0.45 allows roughly 1 typo per 2–3 characters — generous but not noisy.
 *   Lower it (e.g. 0.3) for stricter matching.
 */
function fuzzySearch(query, threshold = 0.45) {
  if (!query.trim()) return [];

  return TOOLS
    .map(tool  => ({ tool, score: scoreTool(tool, query) }))
    .filter(({ score }) => score <= threshold)
    .sort((a, b) => a.score - b.score)
    .map(({ tool }) => tool);
}


// ─── 3. UI WIRING ─────────────────────────────────────────────────────────────
//  Runs once the DOM is ready. Attaches all event listeners.

(function () {
  const input    = document.getElementById('navSearch');
  const dropdown = document.getElementById('searchDropdown');

  // Guard: if the HTML elements aren't present yet, bail gracefully
  if (!input || !dropdown) {
    console.warn('CalCook search: #navSearch or #searchDropdown not found.');
    return;
  }

  let results     = [];  // last set of matched tools
  let activeIndex = -1;  // which item is keyboard-highlighted (-1 = none)

  /** Build and show the dropdown from an array of tool objects */
  function render(items) {
    results     = items;
    activeIndex = -1;

    if (items.length === 0) {
      dropdown.classList.remove('open');
      dropdown.innerHTML = '';
      return;
    }

    dropdown.innerHTML = items.map((tool, i) => `
      <a class="sri" href="${tool.url}" data-index="${i}">
        <span class="sri-icon">${tool.icon}</span>
        <span class="sri-text">
          <strong>${tool.title}</strong>
          <small>${tool.description}</small>
        </span>
      </a>
    `).join('');

    dropdown.classList.add('open');
  }

  /** Highlight one result row for keyboard navigation */
  function setActive(index) {
    const rows = dropdown.querySelectorAll('.sri');
    rows.forEach(el => el.classList.remove('active'));

    if (index >= 0 && index < rows.length) {
      rows[index].classList.add('active');
      activeIndex = index;
    } else {
      activeIndex = -1;
    }
  }

  // ── Event: type in the box → re-run search
  input.addEventListener('input', () => {
    render(fuzzySearch(input.value));
  });

  // ── Event: keyboard navigation (↑ ↓ Enter Escape)
  input.addEventListener('keydown', (e) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        setActive(Math.min(activeIndex + 1, results.length - 1));
        break;
      case 'ArrowUp':
        e.preventDefault();
        setActive(Math.max(activeIndex - 1, 0));
        break;
      case 'Enter':
        if (activeIndex >= 0) {
          e.preventDefault();
          window.location.href = results[activeIndex].url;
        }
        break;
      case 'Escape':
        dropdown.classList.remove('open');
        activeIndex = -1;
        break;
    }
  });

  // ── Event: clicking outside closes the dropdown
  document.addEventListener('click', (e) => {
    if (!input.contains(e.target) && !dropdown.contains(e.target)) {
      dropdown.classList.remove('open');
    }
  });

  // ── Event: clicking back into an already-typed input reopens results
  input.addEventListener('focus', () => {
    if (input.value.trim()) render(fuzzySearch(input.value));
  });
})();
