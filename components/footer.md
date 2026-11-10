---
id: footer
category: footer
tags: [about, bottom, container, footer, links]
source: footer.html
classes: [footer-about, footer-bottom, footer-container, footer-links, footer-section, site-footer]
---

# footer

## HTML

```html
<footer class="site-footer">
  <div class="footer-container">
    
    <div class="footer-section footer-about">
      <h3>CalCook</h3>
      <p>Your home cooking assistant. Tools for everyday-free, no registration needed!</p>
    </div>

    <div class="footer-section footer-links">
      <h3>Legal & Policies</h3>
      <ul>
        <li><a href="privacy-policy.html">Privacy Policy</a></li>
        <li><a href="terms-of-service.html">Terms of Service</a></li>
        <li><a href="disclaimer.html">Disclaimer</a></li>
      </ul>
    </div>

    <div class="footer-section footer-links">
      <h3>Contact Us</h3>
      <ul>
        <li><a href="about.html">About Us</a></li>
        <li><a href="contact.html">Contact Support</a></li>
        <li><a href="sitemap.html">Sitemap</a></li>
      </ul>
    </div>

  </div>

  <div class="footer-bottom">
    <p>&copy; 2026 CalCook. All Rights Reserved.</p>
  </div>
</footer>
```

## CSS

```css
.site-footer {
  padding: 40px 20px 20px 20px;
  font-size: 14px;
}

.footer-container {
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 30px;
}

.footer-section {
  flex: 1;
  min-width: 250px;
}

.footer-section h3 {
  color: var(--clr-dark);
  margin-bottom: 15px;
  font-size: 24px;
}

.footer-section p {
  line-height: 1.6;
}

.footer-section ul {
  list-style: none;
  padding: 0;
  margin: 0;
}

.footer-section ul li {
  margin-bottom: 10px;
}

.footer-section ul li a {
  text-decoration: none;
  transition: color 0.3s ease;
}

.footer-section ul li a:hover {
  text-decoration: underline;
}

.footer-bottom {
  text-align: center;
  margin-top: 40px;
  padding-top: 20px;
  border-top: 1px solid var(--clr-primary);
  font-size: 12px;
}

.site-footer {
  margin-top: 56px;
  background: #fffaf2;
  border-top: 1px solid #eadfce;
  color: var(--clr-dark);
}
```
