---
name: MD2Features
description: "Analyze a website's markdown content for merchant risk and fraud indicators, producing a JSON risk assessment."
user-invocable: true
---
# MD2Features Command

Analyze a website's markdown content for merchant risk and fraud indicators, producing a JSON risk assessment.

Follow the instructions in the reference file:
`/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/Claude/Claude instructions/MD to Features.md`

## Arguments
- `$ARGUMENTS` - The path to the markdown file produced by the /markdown agent (required). This can be either:
  - A full file path to the markdown file
  - A folder path inside `Hackathon/outputs/` (e.g. `www_snowcirculate_com_20260211_150124`) — the agent will find the markdown file inside it

## Instructions

When this command is invoked, execute the following steps:

1. **Locate the markdown file:**
   - If `$ARGUMENTS` is a full path to a `.md` file, use it directly.
   - If `$ARGUMENTS` is a folder name, look for the `_markdown.md` file inside `/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/outputs/$ARGUMENTS/`.
   - If no argument is provided, find the most recently created folder in `Hackathon/outputs/` and use the markdown file inside it.

2. **Read the markdown file** to get the website content.

3. **Run the analysis** using the prompt from the reference file. Act as a Senior Merchant Risk & Fraud Analyst and analyze the markdown content to extract the following into a JSON object:
   - `risk_score`: Integer (1-5, 1=Low risk, 5=High risk)
   - `risk_summary`: String (2-4 sentence explanation)
   - `recommendation`: String ("APPROVE", "DECLINE", or "REVIEW")
   - `has_your_uniqueness_phrase`: Boolean (true if "About Us" uses generic/template language)
   - `vars_compliance`: String ("Full compliant", "Mostly compliant", or "Not compliant")
   - `is_generic_address`: Boolean (true if address is PO Box, virtual office, residential, or missing)
   - `sku_count`: String ("<10", "10-30", or "30+")
   - `has_contact_details`: Boolean (true if phone or email is visible)
   - `is_email_generic`: Boolean (true if contact email uses Gmail/Outlook/Yahoo instead of matching domain)
   - `is_webform_only`: Boolean (true if no other way to contact besides a web form)
   - `language_quality`: Integer (1-5, 1=Poor/Broken, 5=Professional/Native)
   - `is_indexed_on_google`: Boolean (true if content appears SEO-optimized and likely indexed)

4. **Save the output** as a `.json` file in the **same folder** as the markdown file. Use the same naming convention but with `_features.json` suffix.
   - Example: if the markdown is `www_snowcirculate_com_20260211_150124_markdown.md`, save as `www_snowcirculate_com_20260211_150124_features.json`

5. **CRITICAL**: The output file must contain ONLY valid JSON — no markdown formatting, no code fences, no commentary.

6. Confirm to the user that the features were saved, including the filename, location, and a brief summary of the risk assessment.

## Output Location
Features JSON is saved in the same folder as the markdown file:
`/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/outputs/{domain}_{YYYYMMDD_HHMMSS}/`

## Example Usage
```
/MD2Features www_snowcirculate_com_20260211_150124
```
Or with no argument (uses the most recent output):
```
/MD2Features
```