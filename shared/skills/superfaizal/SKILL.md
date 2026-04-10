---
name: superfaizal
description: "SuperFaizal v4 - Claude as the Analyst. Iterative merchant risk assessment with genuine analytical insight."
user-invocable: true
---
SuperFaizal v4 - Claude as the Analyst. Iterative merchant risk assessment with genuine analytical insight.

Input: $ARGUMENTS (a URL, domain, merchant registration ID, company name, or PSP name for PayFac mode)

---

## Phase 0 — Detect input type

Classify the input into one of four modes:

| Input Pattern | Mode | Examples |
|---|---|---|
| Starts with `PSP:` or matches a known PSP name exactly | **PAYFAC** | `PSP:onerway`, `huitech`, `payoneer` |
| Contains a `.` and no spaces, or starts with `http` | **URL** | `parkholiday.com`, `https://example.com/shop` |
| Purely numeric, or matches known reg ID formats | **REG_ID** | `09834481918`, `CY60099629O` |
| Contains spaces, or is clearly a business name | **NAME** | `Park Holidays`, `ZHUODA INTERNATIONAL` |

**Known PSP names** (case-insensitive): asiabill, yinsheng, paydotcom, onerway, hadsund, acqra, payoneer, solidgate, huitech, camelpay, nova2pay, pingpong, useepay, sgepay, alsotrans, evonet, asiapay, bbmsl, paymentoptions, latipay, waffo, pmmax, swiftpass, aleta, qfpay, ezypay, paymentasia, perfectpay, spectra, greenn, kirvano

If the input matches a known PSP name (even without `PSP:` prefix), use **PAYFAC** mode.

---

## Phase 1 — Resolve merchant (MERCHANT modes only)

### PAYFAC mode:
Skip to Phase 2-PF below.

### URL mode:
```sql
SELECT website_domain, merchant_hash_id
FROM prod_agg_worldpay.gold.dim_merchant_websites
WHERE website_domain IN ('<domain>', '<domain_without_www>', 'www.<domain_without_www>')
LIMIT 1
```

### REG_ID mode:
```sql
SELECT merchant_hash_id, merchant_legal_name, merchant_registration_id, psp
FROM prod_agg_worldpay.gold.dim_merchants
WHERE merchant_registration_id = '<input>'
```
If no results: `WHERE merchant_registration_id LIKE '%<input>%'`

### NAME mode:
```sql
SELECT merchant_hash_id, merchant_legal_name, merchant_registration_id, psp
FROM prod_agg_worldpay.gold.dim_merchants
WHERE LOWER(merchant_legal_name) LIKE LOWER('%<input>%')
LIMIT 10
```

If multiple merchants found, list them and ask which one to assess.
If no merchant found, tell the user and stop.

Once you have a `merchant_hash_id`, verify it exists in the dataset:
```bash
python3 -c "
import pandas as pd
df = pd.read_csv('/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/fraud_models_poc/output/unified_merchant_risk_scores.csv')
row = df[df['merchant_hash_id'] == '<MERCHANT_HASH_ID>']
if row.empty: print('NOT_FOUND')
else:
    r = row.iloc[0]
    print(f'FOUND|{r[\"merchant_legal_name\"]}|{r[\"psp\"]}|{r[\"composite_score\"]}|{r[\"composite_tier\"]}|{r[\"n_models_scored\"]}|{r[\"n_models_flagged\"]}')
"
```

If NOT_FOUND, inform user and stop.
If FOUND, tell the user (name, PSP, score, tier) and proceed.

---

## Phase 2 — Extract data dossier

Run the data extraction layer to produce a complete JSON dossier:

```bash
python3 "/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/Claude/superfaizal_data.py" \
  --mode merchant \
  --merchant-hash-id "<MERCHANT_HASH_ID>" \
  --data-dir "/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/fraud_models_poc" \
  --output /tmp/superfaizal_dossier.json
```

Read the JSON output: `/tmp/superfaizal_dossier.json`

---

## Phase 3 — Initial analysis (YOU are the analyst)

You are a senior financial crime analyst at a top-tier consultancy. Read the dossier thoroughly.

**FIRST: Read the `data_quality_notes` section.** These flag known data issues — do NOT cite any figure that has been flagged as invalid.

Focus on what you can directly observe — facts, inconsistencies, and patterns that tell a story. Ignore model scores and percentiles. Think like an investigator, not a data scientist.

1. **What's the story here?** In 2-3 sentences, what kind of merchant is this? What do they sell, where do they operate, and how long have they been active?

2. **What are the 3-5 most noteworthy observations?** Focus on directly observable facts:
   - **Identity**: Does the registered business name match what's on the website? Is there contact information? Does the company registration look legitimate for the stated business?
   - **Transaction patterns**: Are there unusual BIN concentrations (e.g., 80% of transactions from 3 card BINs)? Repeated identical amounts? Geographic mismatches between claimed business location and card issuing countries?
   - **Disputes**: What's the dispute rate? What are the actual reason codes? Are disputes concentrated in fraud-related categories?
   - **Network**: Does this merchant share addresses, emails, phone numbers, or domains with other merchants — especially deactivated ones or ones with known fraud outcomes?
   - **Website content**: Does the website have policies? Does it match the MCC/business category? Are there signs of template/clone sites or brand asset theft?
   - **Check `names_validated` on director_sharing** — if false, the overlap figures are junk data (N/A string matching) and must NOT be cited

3. **What doesn't add up?** Look for inconsistencies — e.g., a high-end electronics site with a $15 average order value, a Hong Kong registered company whose website is entirely in Portuguese, or a "wholesale trading" business with 90% of disputes coded as "merchandise not received."

4. **What don't you know yet?** Specifically:
   - What date ranges do the dispute and transaction figures cover?
   - What are the ACTUAL Visa/Mastercard reason codes (not CSV category labels)?
   - What is the current transaction status (active or dormant)?
   - What does the website actually show? (Plan to crawl it in Phase 4)

Save your analysis to `/tmp/superfaizal_analysis.md`.

---

## Phase 4 — Targeted investigation (iterative loop)

Based on your Phase 3 gaps, investigate further. **Start with the mandatory verification queries below**, then pursue any additional investigation needed.

### MANDATORY: Dispute verification (run FIRST)
The dossier's dispute data comes from an intermediate CSV with no date range and unreliable category labels. You MUST query the source table:

```sql
-- Q1: Actual reason codes with dates
SELECT dispute_reason_code,
       COUNT(*) as cnt,
       SUM(dispute_amount_psp_currency) as total_amount,
       MIN(dispute_submitted_date_time) as earliest,
       MAX(dispute_submitted_date_time) as latest
FROM prod_agg_worldpay.gold.fct_disputes
WHERE merchant_hash_id = '<ID>'
GROUP BY dispute_reason_code
ORDER BY cnt DESC

-- Q2: Resolution status
SELECT dispute_resolution_status, COUNT(*) as cnt
FROM prod_agg_worldpay.gold.fct_disputes
WHERE merchant_hash_id = '<ID>'
GROUP BY dispute_resolution_status
```

**Interpret reason codes using Visa's standard taxonomy:**
- **10.x** = Fraud (10.1 EMV counterfeit, 10.3 card-present fraud, 10.4 card-absent fraud, 10.5 monitoring program)
- **11.x** = Authorisation (11.1 card recovery, 11.2 declined auth, 11.3 no auth)
- **12.x** = Processing errors (12.1 late presentment, 12.5 incorrect amount, 12.6.1 duplicate, 12.6.2 paid other means)
- **13.x** = Consumer disputes (13.1 merchandise not received, 13.2 cancelled recurring, 13.3 not as described, 13.4 counterfeit merchandise, 13.5 misrepresentation, 13.6 credit not processed, 13.7 cancelled merch/services)

**Do NOT use the CSV labels** (fraud_disputes, service_disputes, etc.) — use the actual reason codes from Databricks and the taxonomy above. Note: 13.1 "Merchandise Not Received" is commonly associated with fraud even though it's in the consumer disputes category.

### MANDATORY: Current transaction status
```sql
SELECT transaction_count_28d, dispute_rate_28d, refund_rate_28d, avg_order_value, date_day
FROM prod_agg_worldpay.gold.fct_merchant_metrics
WHERE merchant_hash_id = '<ID>' ORDER BY date_day DESC LIMIT 1
```

### MANDATORY: Director data validation
If `director_sharing.names_validated` is false in the dossier, do NOT cite any director overlap figures. You may optionally check Databricks directly:
```sql
SELECT website_domain, directors
FROM prod_agg_worldpay.gold.fct_risk_website_business_details
WHERE merchant_hash_id = '<ID>'
```
Note: This table's `directors` column is populated by LLM extraction from website content, NOT from company registries. If all values are `["N/A"]` or NULL, there is no director data available.

### Additional investigation tools

**Databricks live queries:**
```sql
-- Merchant details (MCC, country, registration)
SELECT merchant_legal_name, merchant_registration_id, merchant_registered_country, psp
FROM prod_agg_worldpay.gold.dim_merchants
WHERE merchant_hash_id = '<ID>'

-- Website domains
SELECT website_domain
FROM prod_agg_worldpay.gold.dim_merchant_websites
WHERE merchant_hash_id = '<ID>'
```

**Web crawl** — Fetch merchant website via Jina:
```bash
curl -s --max-time 30 \
  -H "Authorization: Bearer $(python3 -c "exec(open('$HOME/Documents/Claude code/credentials.py').read()); print(JINA_TOKEN)")" \
  "https://r.jina.ai/<URL>"
```

**AutoFaizal website assessment** — If the merchant has website URLs, run the AutoFaizal v12 model. Follow the autofaizal skill process:
1. Fetch markdown via Jina
2. Get enrichment data (5 Databricks queries)
3. Run predict_merchant.py
4. Save results to `/tmp/superfaizal_autofaizal.json`

**PSP context** — Use the PSP data already in the dossier's population_context.

**Keep investigating until you have a clear picture.** No fixed number of iterations — stop when you can confidently explain why this merchant matters (or doesn't).

---

## Phase 5 — Synthesis & narrative

You now have a complete picture. Write the report content as structured JSON for the PDF renderer.

**CRITICAL: Write this as a story, not a list. Every claim must be a directly observable fact with a specific data point. No model scores, no percentiles, no statistical measures.**

Save the following JSON to `/tmp/superfaizal_report_content.json`:

```json
{
  "title_page": {
    "merchant_name": "...",
    "psp": "...",
    "status": "LIVE/DEACTIVATED",
    "composite_tier": "CRITICAL",
    "risk_label": "CRITICAL RISK",
    "known_outcomes": ["scheme_exceeded", "fraud_indicator"],
    "one_line_summary": "A single sentence stating the most important observable fact about this merchant."
  },
  "executive_summary": "2-3 paragraphs. Lead with the MOST IMPORTANT observable fact — what would make a Worldpay executive sit up? State what you found, not what a model calculated. Write as if briefing someone who needs to make a decision in 60 seconds.",
  "key_findings": [
    {
      "heading": "Plain English heading (e.g., 'Website Operates Under a Different Name Than the Registered Business')",
      "narrative": "2-4 paragraphs explaining what you observed, why it matters, and what it means in context. Lead with the specific observation, then build the case. Connect dots between different data sources. Every claim backed by a specific data point.",
      "supporting_data": [
        {"observation": "Registered business name", "detail": "ACCESS INFO CO., LIMITED"},
        {"observation": "Name shown on website", "detail": "TechGadgets Store"},
        {"observation": "Contact email on website", "detail": "None found"},
        {"observation": "Physical address on website", "detail": "None — only a Hong Kong PO Box in registration records"}
      ]
    }
  ],
  "risk_overview": {
    "show_scores": false,
    "narrative": "Optional — one paragraph summary of the overall risk picture. Focus on what the data shows in plain language, not model outputs."
  },
  "evidence_sections": [
    {
      "heading": "Dispute Profile",
      "narrative": "Between [date] and [date], the merchant accumulated N disputes totalling $X. The most common reason code was 13.1 (Merchandise Not Received) at N cases, which despite its classification as a 'consumer dispute' is commonly associated with fraud...",
      "data_table": {
        "headers": ["Reason Code", "Description", "Count", "Amount"],
        "rows": [["13.1", "Merchandise Not Received", "325", "$30,875"]]
      }
    }
  ],
  "autofaizal": {
    "included": true,
    "narrative": "What the website assessment reveals...",
    "results": [{"url": "...", "probability": 0.85, "category": "VERY SUSPICIOUS", "features": {}}]
  },
  "methodology_note": "Brief note on data sources, date ranges covered, and any limitations."
}
```

**Writing rules:**
- **State what you can directly observe.** "The registered name is 'ACCESS INFO CO., LIMITED' but the website operates as 'TechGadgets Store' with no mention of the registered entity" — not "The name match score is 0%."
- **No model codes** (M1, M8, etc.). No percentile rankings. No population comparisons using statistical language. No "in the top X%", "Nth percentile", or "X standard deviations above the mean."
- **Plain comparisons are fine when they're intuitive.** "Most merchants in this portfolio have dispute rates below 1%; this merchant's rate is 8.4%" is clear. "This merchant is at the 97.3rd percentile for dispute rate" is not.
- **Be specific.** "78% of all 4,200 transactions used just 3 card BINs, all issued by banks in Taiwan" — not "high BIN concentration."
- **Every claim must have a specific data point.** "338 disputes between 30 Nov 2025 and 4 Feb 2026" not "high dispute rate."
- **Every time-dependent figure must include a date range.** If unknown, say so. Never present a cumulative figure without stating the period.
- **Source attribution.** When citing a figure, you must know which Databricks table (or verified CSV) it came from. If you cannot trace it, do not cite it.
- **Use actual Visa/Mastercard reason codes, not CSV labels.** Cite from `fct_disputes`, classify using the standard taxonomy (10.x=Fraud, 11.x=Auth, 12.x=Processing, 13.x=Consumer disputes).
- **Focus on inconsistencies and anomalies a human would notice.** Name mismatches, geographic mismatches, unusual transaction patterns, website content that doesn't match the stated business.
- **Discard noise.** Only include findings that are genuinely noteworthy. If something looks normal, leave it out.
- **Discard invalid data.** If director_sharing.names_validated is false, do NOT include director overlap findings. If a figure was flagged in data_quality_notes, do NOT cite it.
- **Lead each finding with what you observed**, then explain why it matters.
- **Be insightful, not just informative.** Don't list facts — connect them. If the BIN concentration, dispute pattern, and website all point to card testing, say so. Tell the reader what the facts mean together.
- **Max 5 key finding sections.** Fewer is better if the evidence is thin. Quality over quantity.
- **No "No data found" sections.** Only include sections with actual findings.

---

## Phase 6 — Quality review (MANDATORY self-verification)

Before rendering, re-read your report content and run through EVERY check below. If any check fails, fix the report before proceeding.

### Data provenance checks:
1. **Can every figure be traced to a Databricks source table or verified dataset?** For each number in the report, you must know where it came from. If you can't trace it, remove it.
2. **Does every time-dependent metric state its date range?** Disputes, transactions, refunds — all must include "between [date] and [date]" or "in the 28 days ending [date]". If the period is unknown, say "period unspecified in source data."
3. **Am I using actual Visa/Mastercard reason codes, not CSV category labels?** The CSV labels (fraud_disputes, service_disputes) are unreliable. Use the reason codes from the fct_disputes query.
4. **Is director data validated?** If `names_validated` was false, there must be ZERO references to director overlap, shared directors, or director network in the report.
5. **Are resolution/settlement figures based on actual data?** If all dispute resolutions were NULL, do not claim a settlement rate. Say "resolution status not recorded."

### Analytical quality checks:
6. **Would a non-data-science professional understand every sentence?** If you need to explain what a metric means, rewrite it as an observable fact instead.
7. **Am I stating observable facts, or reporting model outputs?** Every finding should start with something directly observable (a name mismatch, a transaction pattern, a website discrepancy) — not a score or percentile.
8. Is every finding backed by specific data, or am I making vague claims?
9. Did I surface the most important things first?
10. **Am I being insightful?** Am I connecting dots between different observations to tell a coherent story, or just listing facts? The report should explain what the facts MEAN together.
11. Is there anything in the report that reads like a data science paper rather than an analyst briefing? Remove it.
12. **Would a Worldpay executive read this and know what to do next?**

Revise the JSON if needed. Save the final version only after ALL checks pass.

---

## Phase 7 — Generate PDF

```bash
python3 "/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/Claude/superfaizal_report.py" \
  --mode merchant \
  --content-json /tmp/superfaizal_report_content.json \
  --merchant-name "<MERCHANT_NAME>"
```

---

## Phase 8 — Present summary

After PDF generation, provide a terminal summary:

```
SuperFaizal Report Generated
================================
Merchant:     <name>
PSP:          <psp>
Status:       <LIVE/DEACTIVATED>
Risk Level:   <CRITICAL/HIGH/ELEVATED/MODERATE/LOW>

Key Findings:
  1. <one-line observable fact — e.g., "Website operates as 'BrandX' but registered as 'XYZ Trading Ltd'">
  2. <one-line observable fact>
  3. <one-line observable fact>

AutoFaizal v12: <included/not included>
Report: ~/Desktop/SuperFaizal_<name>_<date>.pdf
```

---

## PayFac Mode (Phase 2-PF onwards)

### Phase 2-PF — Extract portfolio dossier

```bash
python3 "/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/Claude/superfaizal_data.py" \
  --mode payfac \
  --psp "<PSP_NAME>" \
  --data-dir "/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/fraud_models_poc" \
  --output /tmp/superfaizal_payfac_dossier.json
```

Read the dossier.

### Phase 3-PF — Portfolio analysis

You are analysing a PayFac's merchant portfolio. Read the dossier and identify:

1. **What makes this PSP's portfolio unusual?** Compare tier distribution, deactivation rate, and mean scores to what you'd expect.
2. **Are there clusters of related merchants?** Look at top merchants — do they share directors, addresses, registration patterns?
3. **What are the dominant risk themes?** All identity issues? All disputes? A mix?
4. **Which 3-5 merchants are the most interesting, and why?** Not just highest-scoring — what's the story?

### Phase 4-PF — Selective deep dives

For the 3-5 most interesting merchants from the portfolio, run individual dossier extractions:

```bash
python3 "/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/Claude/superfaizal_data.py" \
  --mode merchant \
  --merchant-hash-id "<ID>" \
  --data-dir "/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/fraud_models_poc" \
  --output /tmp/superfaizal_merchant_<N>.json
```

Do abbreviated analysis — not full reports. Just enough to write a compelling paragraph about each.

### Phase 5-PF — Portfolio narrative

Write the report content JSON to `/tmp/superfaizal_report_content.json`:

```json
{
  "title_page": {
    "psp_name": "...",
    "n_merchants": 156,
    "portfolio_risk_score": 0.45,
    "one_line_summary": "...",
    "stats_line": "HIGH+CRITICAL: 35 (22.4%)  |  Deactivated: 12 (7.7%)"
  },
  "executive_summary": "2-3 paragraphs. What kind of PSP is this? What are the dominant concerns? What should Worldpay focus on?",
  "risk_distribution": {
    "tiers": {"CRITICAL": 10, "HIGH": 25, "ELEVATED": 30, "MODERATE": 50, "LOW": 41},
    "mean_scores": [
      {"name": "Transaction Concentration", "score": 45.2},
      {"name": "Entity & Network Linkage", "score": 38.1}
    ],
    "narrative": "What the distribution tells us about this PSP."
  },
  "notable_merchants": [
    {
      "name": "...",
      "score": 95.2,
      "tier": "CRITICAL",
      "status": "LIVE",
      "narrative": "One paragraph: why this merchant stands out and what Worldpay should know."
    }
  ],
  "live_critical_high": [
    {"name": "...", "score": 95.2, "tier": "CRITICAL", "n_flagged": 6}
  ],
  "methodology_note": "..."
}
```

### Phase 6-PF — Render

```bash
python3 "/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/Claude/superfaizal_report.py" \
  --mode payfac \
  --content-json /tmp/superfaizal_report_content.json \
  --psp "<PSP_NAME>"
```

---

## Language constraints

Never use these words: alarming, concerning, urgent, suspicious (except in risk category labels from data), recommend, should, must, need to, ought to, clearly, obviously, undoubtedly, red flag.

All numbers must come from actual data. Never estimate or interpolate values.
Where data is unavailable, state "No data available."