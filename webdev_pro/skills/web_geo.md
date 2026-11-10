# Skill: Generative Engine Optimization (GEO)

When building a website, you MUST optimize it for AI crawlers (Perplexity, ChatGPT, Claude) using `llms.txt` and JSON-LD.

## 1. The `llms.txt` File
If the task involves building a website, you MUST generate a file named `llms.txt` in the project root.
Format it EXACTLY like this:
- **H1**: The name of the website/company.
- **Blockquote**: A 1-2 sentence summary of what the site does (this is the "answer" AI will read).
- **H2 Sections**: Group links by category (e.g., `## Products`, `## About`, `## Blog`).
- **List Items**: Provide Markdown links with a brief description (e.g., `- [Pricing](https://site.com/pricing): Simple flat-rate plans for startups.`).

Example `llms.txt`:
```markdown
# DifferiX

> The local-first OS for autonomous AI agents. It verifies code deterministically.

## Documentation
- [Quickstart](https://differix.dev/quickstart): Install and run in 2 minutes.
- [Architecture](https://differix.dev/arch): How the TCA and A-Score work.

## Plugins
- [WebDev Pro](https://differix.dev/plugins/webdev): Generates Awwwards-level UI.
2. JSON-LD Schema Injection

In index.html, you MUST inject <script type="application/ld+json"> into the <head>.
Include at least one of these schemas depending on the site type:

    WebSite: For the homepage.

    Organization: For the brand.

    FAQPage: If there are FAQs (Crucial for AI extraction).

    Product or Article: For e-commerce or blogs.

Example FAQPage Schema:
html

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [{
    "@type": "Question",
    "name": "What is DifferiX?",
    "acceptedAnswer": {
      "@type": "Answer",
      "text": "DifferiX is a deterministic OS for AI agents that verifies code."
    }
  }]
}
</script>

3. Answer-First Content Structure

    Every main section MUST start with a direct, concise 1-2 sentence answer before adding fluff.

    AI models extract the first sentence of a section. Make it count.