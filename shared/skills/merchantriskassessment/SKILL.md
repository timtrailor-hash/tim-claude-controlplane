---
name: merchantriskassessment
description: "Run the full end-to-end merchant risk assessment pipeline for a given website URL."
user-invocable: true
---
# Merchant Risk Assessment Command

Run the full end-to-end merchant risk assessment pipeline for a given website URL.

## Arguments
- `$ARGUMENTS` - The website URL to assess (required)

## Instructions

When this command is invoked, execute the following steps **in the exact order specified**, respecting the parallel/sequential constraints.

### Phase 1 — Data Collection (run in parallel)

Run these two steps simultaneously:

**1a. Screenshot agent:**
```bash
URL="$ARGUMENTS"
case "$URL" in http://*|https://*) ;; *) URL="https://$URL" ;; esac
FILENAME=$(echo "$URL" | sed -E 's|https?://||' | sed 's|/.*||' | sed 's/[^a-zA-Z0-9]/_/g')
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
OUTDIR="/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/outputs/${FILENAME}_${TIMESTAMP}"
mkdir -p "$OUTDIR"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars --screenshot="${OUTDIR}/${FILENAME}_${TIMESTAMP}_screenshot.png" --window-size=1920,8000 "$URL"
```

**1b. Markdown agent:**
```bash
curl -s -H "Authorization: Bearer $(python3 -c "exec(open('$HOME/Documents/Claude code/credentials.py').read()); print(JINA_TOKEN)")" "https://r.jina.ai/${URL}" -o "${OUTDIR}/${FILENAME}_${TIMESTAMP}_markdown.md"
```

**IMPORTANT:** Both must use the SAME `OUTDIR`, `FILENAME`, and `TIMESTAMP` values. Create the folder and variables ONCE before running both.

**WAIT for both to complete before proceeding to Phase 2.**

---

### Phase 2 — Analysis (run in parallel)

Run these three steps simultaneously, all reading from/writing to the same `OUTDIR`:

**2a. MD2Features:**
- Read the `_markdown.md` file from the output folder
- Analyse the content for merchant risk/fraud indicators as per the MD2Features agent instructions
- Save as `${FILENAME}_${TIMESTAMP}_features.json` in `OUTDIR`

**2b. Transaction Metrics:**
- Extract the website URL from the markdown file
- Query Databricks (`prod_agg_worldpay.gold`) for transaction metrics, website age, and dispute metrics
- Save as `${FILENAME}_${TIMESTAMP}_transaction_metrics.json` in `OUTDIR`

**2c. Claude Direct to Features:**
- Run the full risk assessment with three-tier page discovery fallback (common URLs → markdown links → sitemap)
- Fetch sitemap, homepage, subpages, check Google indexing, reviews, domain registration
- Save as `${FILENAME}_${TIMESTAMP}_risk_assessment.json` in `OUTDIR`

**WAIT for all three to complete before proceeding to Phase 3.**

---

### Phase 3 — Merge Output

**3. Create Output:**
- Read the three JSON files: `_features.json`, `_transaction_metrics.json`, `_risk_assessment.json`
- Merge them into a single file with this structure:
```json
{
  "website_url": "",
  "domain": "",
  "timestamp": "",
  "md2features": {},
  "transaction_metrics": {},
  "website_age": {},
  "dispute_metrics": {},
  "risk_assessment": {}
}
```
- Save as `${FILENAME}_${TIMESTAMP}_output.json` in `OUTDIR`

---

### Phase 4 — Confirm

Report to the user:
- The output folder path
- List of all files created (screenshot, markdown, features, transaction metrics, risk assessment, merged output)
- The overall risk score and recommendation from each assessment
- A brief summary of key findings

## Output Location
All files are saved to:
`/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/outputs/{domain}_{YYYYMMDD_HHMMSS}/`

## Example Usage
```
/merchantriskassessment www.snowcirculate.com
```