---
id: translate
category: language
tags: [language, translate]
source: translate.html
classes: []
---

# translate

## HTML

```html
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
                justify-content: start;
                background: #ffffff;
                border-radius: 9999px;
                padding: 5px 12px 5px 7px;
                box-shadow: 0 3px 10px rgba(43, 34, 23, 0.1);
                cursor: pointer;
                gap: 8px;
                border: 2px solid var(--clr-primary);
                height: 35px;
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
            <!-- Language options (same as before) -->
            <div
                onclick="selectLanguage('en')"
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
                onclick="selectLanguage('de')"
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
                onclick="selectLanguage('pt')"
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
                onclick="selectLanguage('es')"
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
                onclick="selectLanguage('fr')"
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
                onclick="selectLanguage('ru')"
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
                onclick="selectLanguage('ja')"
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

        <!-- Hidden Google Translate Widget -->
        <div id="google_translate_element" style="display: none !important"></div>

        <script>
            let currentLang = "en";

            function toggleLanguageDropdown() {
            const dropdown = document.getElementById("lang-dropdown");
            dropdown.style.display =
                dropdown.style.display === "block" ? "none" : "block";
            }

            function selectLanguage(lang) {
            currentLang = lang;

            // Update current flag
            const flagMap = {
                en: "https://i.ibb.co/LdVXKyd8/en.png",
                de: "https://i.ibb.co/p6gBMz6J/de.webp",
                pt: "https://i.ibb.co/r27gK3dW/pt.png",
                es: "https://i.ibb.co/7dJb6cF9/es.png",
                fr: "https://i.ibb.co/G4D61PHs/fr.png",
                ru: "https://i.ibb.co/35hr9fcy/ru.webp",
                ja: "https://i.ibb.co/LdGCJQHW/jp.png",
            };
            document.getElementById("current-flag").src = flagMap[lang];

            // Trigger Google Translate
            const select = document.querySelector(".goog-te-combo");
            if (select) {
                select.value = lang;
                select.dispatchEvent(new Event("change"));
            }

            // Close dropdown
            document.getElementById("lang-dropdown").style.display = "none";
            }

            // Close dropdown when clicking outside
            document.addEventListener("click", function (e) {
            const selector = document.getElementById("custom-lang-selector");
            if (!selector.contains(e.target)) {
                document.getElementById("lang-dropdown").style.display = "none";
            }
            });

            // Initialize Google Translate
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
        </script>

        <script
            type="text/javascript"
            src="https://translate.google.com/translate_a/element.js?cb=googleTranslateElementInit"
        ></script>
```

## CSS

```css
/* no matching CSS rules */
```

## JavaScript

```javascript
let currentLang = "en";

            function toggleLanguageDropdown() {
            const dropdown = document.getElementById("lang-dropdown");
            dropdown.style.display =
                dropdown.style.display === "block" ? "none" : "block";
            }

            function selectLanguage(lang) {
            currentLang = lang;

            // Update current flag
            const flagMap = {
                en: "https://i.ibb.co/LdVXKyd8/en.png",
                de: "https://i.ibb.co/p6gBMz6J/de.webp",
                pt: "https://i.ibb.co/r27gK3dW/pt.png",
                es: "https://i.ibb.co/7dJb6cF9/es.png",
                fr: "https://i.ibb.co/G4D61PHs/fr.png",
                ru: "https://i.ibb.co/35hr9fcy/ru.webp",
                ja: "https://i.ibb.co/LdGCJQHW/jp.png",
            };
            document.getElementById("current-flag").src = flagMap[lang];

            // Trigger Google Translate
            const select = document.querySelector(".goog-te-combo");
            if (select) {
                select.value = lang;
                select.dispatchEvent(new Event("change"));
            }

            // Close dropdown
            document.getElementById("lang-dropdown").style.display = "none";
            }

            // Close dropdown when clicking outside
            document.addEventListener("click", function (e) {
            const selector = document.getElementById("custom-lang-selector");
            if (!selector.contains(e.target)) {
                document.getElementById("lang-dropdown").style.display = "none";
            }
            });

            // Initialize Google Translate
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
```
