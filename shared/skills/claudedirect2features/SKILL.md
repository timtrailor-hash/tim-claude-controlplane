---
name: claudedirect2features
description: "Run a full merchant website risk assessment by directly fetching and analysing a website, with three-tier fallback for page discovery."
user-invocable: true
---
# Claude Direct to Features Command

Run a full merchant website risk assessment by directly fetching and analysing a website, with three-tier fallback for page discovery.

Follow the instructions in the reference file:
`/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/Claude/Claude instructions/claudedirect_to_features.md`

## Arguments
- `$ARGUMENTS` - The website URL to assess (required)

## Instructions

When this command is invoked, execute the following steps:

1. **Prepare the URL and output folder:**
   - If no URL is provided in `$ARGUMENTS`, ask the user for one.
   - Add `https://` if no protocol is present.
   - Extract the domain from the URL.
   - Check if an existing folder for this domain exists in `Hackathon/outputs/`. If so, use it. Otherwise create a new one:
     ```
     /Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/outputs/{domain}_{YYYYMMDD_HHMMSS}/
     ```

2. **Fetch the sitemap** (`/sitemap.xml`, fallback to `/sitemap_index.xml` and `/robots.txt`). Parse and store all URLs as a reference for fallback page discovery.

3. **Fetch the homepage** and extract all visible content: navigation, products, footer links, contact info, policy links, company descriptions.

4. **Fetch key subpages using the three-tier fallback:**

   For each of these pages: About Us, Contact, Refund Policy, Terms of Service, Privacy Policy, Shipping Policy, All Products:

   - **Tier 1**: Try common URL patterns (e.g. `/pages/about-us`, `/policies/refund-policy`, etc.)
   - **Tier 2**: If 404, search the markdown agent output (`_markdown.md` in the output folder or most recent matching folder in `Hackathon/outputs/`) for links to the missing page
   - **Tier 3**: If still not found, search the sitemap URLs for matching keywords (`refund`, `terms`, `privacy`, `about`, `contact`, etc.)
   - Only mark as "not found" if all three tiers fail

5. **Estimate product count** using `/collections/all`, sitemap product URLs, or markdown agent output as fallbacks.

6. **Check Google indexing** — web search for `site:{domain}`.

7. **Search for external reviews** — web search for `"{domain}" reviews scam legit trustpilot`.

8. **Check domain registration** — fetch `urlscan.io/domain/{domain}` for creation date, registrant country, hosting info, threats.

9. **Evaluate risk indicators** and generate a JSON file with this schema:

```json
{
  "risk_score": 0,
  "risk_summary": "",
  "recommendation": "",
  "has_your_uniqueness_phrase": false,
  "vars_compliance": "",
  "is_generic_address": false,
  "sku_count": "",
  "has_contact_details": false,
  "is_email_generic": false,
  "is_webform_only": false,
  "language_quality": 0,
  "is_indexed_on_google": false
}
```

10. **Save the JSON** to the output folder as `{domain}_{TIMESTAMP}_risk_assessment.json`. The file must contain ONLY valid JSON.

11. Confirm to the user: filename, location, risk score, recommendation, and a brief summary of key findings.

## Value Constraints
- `risk_score`: Integer 1-5 (1=Low, 5=High)
- `recommendation`: `"APPROVE"`, `"REVIEW"`, or `"DECLINE"`
- `vars_compliance`: `"Full compliant"`, `"Mostly compliant"`, or `"Not compliant"`
- `sku_count`: `"<10"`, `"10-30"`, or `"30+"`
- `language_quality`: Integer 1-5 (1=Poor/Broken, 5=Professional/Native)
- Use `null` for any value that cannot be determined

## Output Location
```
/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/outputs/{domain}_{YYYYMMDD_HHMMSS}/
```

## Example Usage
```
/claudedirect2features www.snowcirculate.com
```