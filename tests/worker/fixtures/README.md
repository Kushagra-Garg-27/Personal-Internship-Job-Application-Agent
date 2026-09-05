# Platform HTML & API Fixtures Maintenance Notice

> [!WARNING]
> **Fixtures Go Stale When Platforms Redesign**:
> Web-based experimental adapters (`internshala`, `unstop`) rely on CSS selectors and DOM structures
> that change whenever the target platforms update their frontend user interfaces.
> 
> Testing against live sites in CI/CD is fragile and violates rate-limits. Therefore, integration tests
> run against the anonymized HTML and API fixtures stored in this directory.
>
> **Recurring Maintenance Protocol**:
> 1. If an adapter breaks in production due to a platform UI redesign, capture the updated page HTML using Playwright: `page.content()`.
> 2. Anonymize all personal data, email addresses, phone numbers, and company tokens.
> 3. Save the anonymized snapshot into this directory (`internshala_form.html` or `unstop_form.html`).
> 4. Update the adapter selectors and verify integration tests pass.
