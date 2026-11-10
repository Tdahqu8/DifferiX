---
id: header
category: button
tags: [button, dropdown, hamburger, header, icon, links, nav, search]
source: header.html
classes: [dropdown, nav-hamburger, nav-links, nav-search-icon, nav-search-input, nav-search-wrap, search-dropdown]
---

# header

## HTML

```html
<!-- ===== NAV ===== -->
    <nav>
      <!-- CalCook Custom Language Selector -->
      <div
        id="custom-lang-selector"
        style="
          display: inline-block;
          margin-left: 12px;
          vertical-align: middle;
          position: relative;
          z-index: 100;
        "
      >
        <!-- Trigger Pill -->
        <div
          onclick="toggleLanguageDropdown()"
          style="
            display: flex;
            align-items: center;
            background: #ffffff;
            border-radius: 9999px;
            padding: 5px 12px 5px 7px;
            box-shadow: 0 3px 10px rgba(43, 34, 23, 0.1);
            cursor: pointer;
            gap: 8px;
            border: 2px solid var(--clr-primary);
            height: 42px;
            transition: all 0.2s ease;
          "
        >
          <img
            id="current-flag"
            src="https://i.ibb.co/LdVXKyd8/en.png"
            alt="English"
            style="
              width: 26px;
              height: 26px;
              border-radius: 50%;
              object-fit: cover;
            "
          />

          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            <path
              d="M6 9L12 15L18 9"
              stroke="#2b2217"
              stroke-width="3"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
        </div>

        <!-- Dropdown Menu -->
        <div
          id="lang-dropdown"
          style="
            display: none;
            position: absolute;
            top: 52px;
            left: 0;
            background: #ffffff;
            border: 2px solid var(--clr-primary);
            border-radius: 20px;
            box-shadow: 0 10px 30px rgba(43, 34, 23, 0.15);
            min-width: 200px;
            overflow: hidden;
            z-index: 200;
            animation: dropdownPop 0.25s ease forwards;
          "
        >
          <div
            onclick="restoreNativeLanguage(event)"
            style="
              padding: 12px 16px;
              display: flex;
              align-items: center;
              gap: 12px;
              cursor: pointer;
              transition: background 0.2s;
            "
          >
            <img
              src="https://i.ibb.co/LdVXKyd8/en.png"
              style="width: 26px; height: 26px; border-radius: 50%"
            />
            <span
              style="
                font-family: var(--ff-label, &quot;Yeseva One&quot;), serif;
                font-size: 1.05rem;
              "
              >English</span
            >
          </div>
          <div
            onclick="changeLanguage(event, 'de')"
            style="
              padding: 12px 16px;
              display: flex;
              align-items: center;
              gap: 12px;
              cursor: pointer;
              transition: background 0.2s;
            "
          >
            <img
              src="https://i.ibb.co/p6gBMz6J/de.webp"
              style="width: 26px; height: 26px; border-radius: 50%"
            />
            <span
              style="
                font-family: var(--ff-label, &quot;Yeseva One&quot;), serif;
                font-size: 1.05rem;
              "
              >Deutsch</span
            >
          </div>
          <div
            onclick="changeLanguage(event, 'pt')"
            style="
              padding: 12px 16px;
              display: flex;
              align-items: center;
              gap: 12px;
              cursor: pointer;
              transition: background 0.2s;
            "
          >
            <img
              src="https://i.ibb.co/r27gK3dW/pt.png"
              style="width: 26px; height: 26px; border-radius: 50%"
            />
            <span
              style="
                font-family: var(--ff-label, &quot;Yeseva One&quot;), serif;
                font-size: 1.05rem;
              "
              >Português</span
            >
          </div>
          <div
            onclick="changeLanguage(event, 'es')"
            style="
              padding: 12px 16px;
              display: flex;
              align-items: center;
              gap: 12px;
              cursor: pointer;
              transition: background 0.2s;
            "
          >
            <img
              src="https://i.ibb.co/7dJb6cF9/es.png"
              style="width: 26px; height: 26px; border-radius: 50%"
            />
            <span
              style="
                font-family: var(--ff-label, &quot;Yeseva One&quot;), serif;
                font-size: 1.05rem;
              "
              >Español</span
            >
          </div>
          <div
            onclick="changeLanguage(event, 'fr')"
            style="
              padding: 12px 16px;
              display: flex;
              align-items: center;
              gap: 12px;
              cursor: pointer;
              transition: background 0.2s;
            "
          >
            <img
              src="https://i.ibb.co/G4D61PHs/fr.png"
              style="width: 26px; height: 26px; border-radius: 50%"
            />
            <span
              style="
                font-family: var(--ff-label, &quot;Yeseva One&quot;), serif;
                font-size: 1.05rem;
              "
              >Français</span
            >
          </div>
          <div
            onclick="changeLanguage(event, 'ru')"
            style="
              padding: 12px 16px;
              display: flex;
              align-items: center;
              gap: 12px;
              cursor: pointer;
              transition: background 0.2s;
            "
          >
            <img
              src="https://i.ibb.co/35hr9fcy/ru.webp"
              style="width: 26px; height: 26px; border-radius: 50%"
            />
            <span
              style="
                font-family: var(--ff-label, &quot;Yeseva One&quot;), serif;
                font-size: 1.05rem;
              "
              >Русский</span
            >
          </div>
          <div
            onclick="changeLanguage(event, 'ja')"
            style="
              padding: 12px 16px;
              display: flex;
              align-items: center;
              gap: 12px;
              cursor: pointer;
              transition: background 0.2s;
            "
          >
            <img
              src="https://i.ibb.co/LdGCJQHW/jp.png"
              style="width: 26px; height: 26px; border-radius: 50%"
            />
            <span
              style="
                font-family: var(--ff-label, &quot;Yeseva One&quot;), serif;
                font-size: 1.05rem;
              "
              >日本語</span
            >
          </div>
        </div>
      </div>

      <!-- Google Translate Widget -->
      <div
        id="google_translate_element"
        style="
          position: absolute;
          width: 1px;
          height: 1px;
          overflow: hidden;
          opacity: 0;
          pointer-events: none;
        "
      ></div>

      <script>
        function changeLanguage(event, langCode) {
          // 1. Prevent the default anchor click jump
          event.preventDefault();

          // 2. Format the cookie value (assumes your native source language is English 'en')
          var cookieValue = "/en/" + langCode;

          // 3. Set the cookie across all potential domain variations
          var domain = window.location.hostname;
          document.cookie = "googtrans=" + cookieValue + "; path=/;";
          document.cookie =
            "googtrans=" + cookieValue + "; path=/; domain=" + domain;
          document.cookie =
            "googtrans=" +
            cookieValue +
            "; path=/; domain=." +
            domain.split(".").slice(-2).join(".");

          // 4. Force an immediate page reload to apply the language
          window.location.reload();
        }
        function restoreNativeLanguage(event) {
          // Prevent the default anchor link jump behavior
          event.preventDefault();

          // 1. Clear the Google Translate cookies for all likely domain paths
          document.cookie =
            "googtrans=; expires=Thu, 01 Jan 1770 00:00:00 UTC; path=/;";
          document.cookie =
            "googtrans=; expires=Thu, 01 Jan 1770 00:00:00 UTC; path=/; domain=" +
            document.domain;
          document.cookie =
            "googtrans=; expires=Thu, 01 Jan 1770 00:00:00 UTC; path=/; domain=." +
            document.domain.split(".").slice(-2).join(".");

          // 2. Remove the hash parameter from the current URL if it exists
          if (window.location.hash.includes("googtrans")) {
            window.location.hash = "";
          }

          // 3. Reload the page to apply the native language state
          window.location.reload();
        }
        let currentLang = "en";

        function toggleLanguageDropdown() {
          const dropdown = document.getElementById("lang-dropdown");
          dropdown.style.display =
            dropdown.style.display === "block" ? "none" : "block";
        }

        // Update flag based on current Google Translate cookie
        function updateFlagFromCookie() {
          const match = document.cookie.match(/googtrans=([^;]+)/);
          if (match) {
            const value = decodeURIComponent(match[1]);
            const lang = value.split("/").pop(); // get target language

            const flagMap = {
              en: "https://i.ibb.co/LdVXKyd8/en.png",
              de: "https://i.ibb.co/p6gBMz6J/de.webp",
              pt: "https://i.ibb.co/r27gK3dW/pt.png",
              es: "https://i.ibb.co/7dJb6cF9/es.png",
              fr: "https://i.ibb.co/G4D61PHs/fr.png",
              ru: "https://i.ibb.co/35hr9fcy/ru.webp",
              ja: "https://i.ibb.co/LdGCJQHW/jp.png",
            };

            if (flagMap[lang]) {
              document.getElementById("current-flag").src = flagMap[lang];
              currentLang = lang;
            }
          }
        }

        function changeLanguage(event, langCode) {
          event.preventDefault();

          const cookieValue = "/en/" + langCode;
          const domain = window.location.hostname;

          document.cookie = "googtrans=" + cookieValue + "; path=/;";
          document.cookie =
            "googtrans=" + cookieValue + "; path=/; domain=" + domain;
          document.cookie =
            "googtrans=" +
            cookieValue +
            "; path=/; domain=." +
            domain.split(".").slice(-2).join(".");

          window.location.reload();
        }

        function restoreNativeLanguage(event) {
          event.preventDefault();

          document.cookie =
            "googtrans=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
          document.cookie =
            "googtrans=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/; domain=" +
            document.domain;

          window.location.reload();
        }

        // Initialize flag on page load
        document.addEventListener("DOMContentLoaded", () => {
          updateFlagFromCookie();
        });

        // Trigger translation (waits/retries until the widget is ready)
        setTranslateLanguage(lang);

        document.getElementById("lang-dropdown").style.display = "none";

        // Close dropdown when clicking outside
        document.addEventListener("click", function (e) {
          const selector = document.getElementById("custom-lang-selector");
          if (!selector.contains(e.target)) {
            document.getElementById("lang-dropdown").style.display = "none";
          }
        });
      </script>

      <style>
        #lang-dropdown div:hover {
          background: #fff8e7 !important;
        }
        @keyframes dropdownPop {
          from {
            opacity: 0;
            transform: translateY(-10px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
      </style>

      <div class="nav-search-wrap">
        <img
          class="nav-search-icon"
          src="https://i.ibb.co/FbvSRkhV/search-Icon.png"
          alt="🔍"
        />
        <input
          class="nav-search-input"
          id="navSearch"
          type="text"
          placeholder="CalCook smart tools"
          autocomplete="off"
          aria-label="Search"
        />
        <div id="searchDropdown" class="search-dropdown"></div>
      </div>

      <ul class="nav-links">
        <li><a href="scaler.html">Calculators</a></li>
        <li><a href="converter.html">Unit converters</a></li>
        <li><a href="ratio.html">Tools</a></li>
      </ul>

      <button class="nav-hamburger" id="menuToggle" aria-label="Open menu">
        ≡
      </button>

      <div class="dropdown" id="dropdown" role="menu">
        <a href="#" role="menuitem">About us</a>
        <a href="#" role="menuitem">FAQ</a>
        <a href="#" role="menuitem">Privacy policies</a>
      </div>
    </nav>

    <script>
      const toggle = document.getElementById("menuToggle");
      const dropdown = document.getElementById("dropdown");
      toggle.addEventListener("click", (e) => {
        e.stopPropagation();
        const open = dropdown.classList.toggle("open");
        toggle.setAttribute("aria-expanded", open);
      });
      document.addEventListener("click", () => {
        dropdown.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      });
      const inp = document.getElementById("navSearch");
      inp.addEventListener("input", () => {
        inp.placeholder = inp.value.length ? "" : "CalCook smart tools";
      });
    </script>
    <script src="search.js"></script>
```

## CSS

```css
.nav-search-wrap {
  position: relative;
display: flex;
align-items: center;
}

.nav-search-input {
  background: var(--clr-white);
border: none;
border-radius: 50px;
padding: 8px 18px 8px 42px;
font-family: var(--ff-label);
font-size: 0.88rem;
color: var(--clr-dark);
width: 240px;
outline: none;
}

.nav-search-input::placeholder {
  color: var(--clr-gray);
}

.nav-search-icon {
  position: absolute;
left: 13px;
width: 17px;
height: 17px;
opacity: 0.55;
pointer-events: none;
}

.nav-links {
  display: flex;
gap: 36px;
list-style: none;
align-items: center;
}

.nav-links a {
  font-family: var(--ff-label);
font-size: 0.9rem;
color: var(--clr-dark);
text-decoration: none;
transition: opacity 0.2s;
}

.nav-links a:hover {
  opacity: 0.55;
}

.nav-hamburger {
  display: flex;
align-items: center;
justify-content: center;
cursor: pointer;
background: none;
border: none;
font-size: 1.5rem;
color: var(--clr-dark);
line-height: 1;
padding: 4px 6px;
}

.dropdown {
  position: absolute;
top: var(--nav-h);
right: 0;
width: 220px;
background: var(--clr-primary);
border-radius: 0 0 16px 16px;
box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
overflow: hidden;
max-height: 0;
transition: max-height 0.38s cubic-bezier(0.4, 0, 0.2, 1);
z-index: 50;
}

.dropdown.open {
  max-height: 220px;
}

.dropdown a {
  display: block;
padding: 14px 24px;
font-family: var(--ff-label);
font-size: 1rem;
color: var(--clr-white);
text-decoration: none;
transition: background 0.2s;
}

.dropdown a:hover {
  background: rgba(0, 0, 0, 0.08);
}

.search-dropdown {
  position: absolute;
top: calc(100% + 6px);
left: 0;
width: 100%;
min-width: 280px;
background: var(--clr-white);
border-radius: 14px;
box-shadow: 0 8px 28px rgba(0, 0, 0, 0.14);
overflow: hidden;
max-height: 0;
opacity: 0;
pointer-events: none;
transition:
max-height 0.25s ease,
opacity 0.2s ease;
z-index: 100;
}

.search-dropdown.open {
  max-height: 320px;
opacity: 1;
pointer-events: auto;
}

@media (max-width: 860px) {
  .nav-links {
    display: none;
  }
  .nav-search-input {
    width: 60vw;
    font-size: 0.85rem;
  }
}

@media (max-width: 520px) {
  .nav-search-wrap {
    width: fit-content;
  }
  .nav-search-input {
    width: 60vw;
    font-size: 0.85rem;
  }
  .nav-search-icon {
    width: 15px;
    height: 15px;
    left: 9px;
  }
}
```

## JavaScript

```javascript
function googleTranslateElementInit() {
        new google.translate.TranslateElement(
          {
            pageLanguage: "en",
            includedLanguages: "de,pt,es,fr,ru,ja",
            layout: google.translate.TranslateElement.InlineLayout.SIMPLE,
            autoDisplay: false,
          },
          "google_translate_element",
        );
      }

function changeLanguage(event, langCode) {
          // 1. Prevent the default anchor click jump
          event.preventDefault();

          // 2. Format the cookie value (assumes your native source language is English 'en')
          var cookieValue = "/en/" + langCode;

          // 3. Set the cookie across all potential domain variations
          var domain = window.location.hostname;
          document.cookie = "googtrans=" + cookieValue + "; path=/;";
          document.cookie =
            "googtrans=" + cookieValue + "; path=/; domain=" + domain;
          document.cookie =
            "googtrans=" +
            cookieValue +
            "; path=/; domain=." +
            domain.split(".").slice(-2).join(".");

          // 4. Force an immediate page reload to apply the language
          window.location.reload();
        }
        function restoreNativeLanguage(event) {
          // Prevent the default anchor link jump behavior
          event.preventDefault();

          // 1. Clear the Google Translate cookies for all likely domain paths
          document.cookie =
            "googtrans=; expires=Thu, 01 Jan 1770 00:00:00 UTC; path=/;";
          document.cookie =
            "googtrans=; expires=Thu, 01 Jan 1770 00:00:00 UTC; path=/; domain=" +
            document.domain;
          document.cookie =
            "googtrans=; expires=Thu, 01 Jan 1770 00:00:00 UTC; path=/; domain=." +
            document.domain.split(".").slice(-2).join(".");

          // 2. Remove the hash parameter from the current URL if it exists
          if (window.location.hash.includes("googtrans")) {
            window.location.hash = "";
          }

          // 3. Reload the page to apply the native language state
          window.location.reload();
        }
        let currentLang = "en";

        function toggleLanguageDropdown() {
          const dropdown = document.getElementById("lang-dropdown");
          dropdown.style.display =
            dropdown.style.display === "block" ? "none" : "block";
        }

        // Update flag based on current Google Translate cookie
        function updateFlagFromCookie() {
          const match = document.cookie.match(/googtrans=([^;]+)/);
          if (match) {
            const value = decodeURIComponent(match[1]);
            const lang = value.split("/").pop(); // get target language

            const flagMap = {
              en: "https://i.ibb.co/LdVXKyd8/en.png",
              de: "https://i.ibb.co/p6gBMz6J/de.webp",
              pt: "https://i.ibb.co/r27gK3dW/pt.png",
              es: "https://i.ibb.co/7dJb6cF9/es.png",
              fr: "https://i.ibb.co/G4D61PHs/fr.png",
              ru: "https://i.ibb.co/35hr9fcy/ru.webp",
              ja: "https://i.ibb.co/LdGCJQHW/jp.png",
            };

            if (flagMap[lang]) {
              document.getElementById("current-flag").src = flagMap[lang];
              currentLang = lang;
            }
          }
        }

        function changeLanguage(event, langCode) {
          event.preventDefault();

          const cookieValue = "/en/" + langCode;
          const domain = window.location.hostname;

          document.cookie = "googtrans=" + cookieValue + "; path=/;";
          document.cookie =
            "googtrans=" + cookieValue + "; path=/; domain=" + domain;
          document.cookie =
            "googtrans=" +
            cookieValue +
            "; path=/; domain=." +
            domain.split(".").slice(-2).join(".");

          window.location.reload();
        }

        function restoreNativeLanguage(event) {
          event.preventDefault();

          document.cookie =
            "googtrans=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
          document.cookie =
            "googtrans=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/; domain=" +
            document.domain;

          window.location.reload();
        }

        // Initialize flag on page load
        document.addEventListener("DOMContentLoaded", () => {
          updateFlagFromCookie();
        });

        // Trigger translation (waits/retries until the widget is ready)
        setTranslateLanguage(lang);

        document.getElementById("lang-dropdown").style.display = "none";

        // Close dropdown when clicking outside
        document.addEventListener("click", function (e) {
          const selector = document.getElementById("custom-lang-selector");
          if (!selector.contains(e.target)) {
            document.getElementById("lang-dropdown").style.display = "none";
          }
        });

const toggle = document.getElementById("menuToggle");
      const dropdown = document.getElementById("dropdown");
      toggle.addEventListener("click", (e) => {
        e.stopPropagation();
        const open = dropdown.classList.toggle("open");
        toggle.setAttribute("aria-expanded", open);
      });
      document.addEventListener("click", () => {
        dropdown.classList.remove("open");
        toggle.setAttribute("aria-expanded", "false");
      });
      const inp = document.getElementById("navSearch");
      inp.addEventListener("input", () => {
        inp.placeholder = inp.value.length ? "" : "CalCook smart tools";
      });
```
