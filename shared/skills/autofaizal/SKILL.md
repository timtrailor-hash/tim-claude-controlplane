---
name: autofaizal
description: "AutoFaizal -- Automated merchant risk assessment using the v12 ML model (two-model blend GBM + sigmoid, 16 features, is_app_store capped at ~15% influence, txn/visit ratio, trained on 539 merchants with real website data)."
user-invocable: true
---
AutoFaizal — Automated merchant risk assessment using the v12 ML model (two-model blend GBM + sigmoid, 16 features, is_app_store capped at ~15% influence, txn/visit ratio, trained on 539 merchants with real website data).

Input: $ARGUMENTS (a URL, domain, merchant registration ID, or company name)

---

## Step 0 — Detect input type

Classify the input into one of three modes:

| Input Pattern | Mode | Examples |
|---|---|---|
| Contains a `.` and no spaces, or starts with `http` | **URL** | `parkholiday.com`, `https://example.com/shop` |
| Purely numeric, or matches known reg ID formats (e.g., CNPJ `XX.XXX.XXX/XXXX-XX` or `XXXXXXXXXXX`, UK company number, alphanumeric IDs like `CY10409949F`) | **REG_ID** | `09834481918`, `40746127000179`, `CY60099629O`, `16036202` |
| Contains spaces, or is clearly a business name | **NAME** | `Park Holidays`, `Spiritual Nebula Limited`, `TENORSHARE` |

**Ambiguous cases:** If the input is a short alphanumeric string without dots or spaces (e.g., `ABC123`), try REG_ID first. If no results, fall back to NAME.

---

## Step 1 — Resolve merchant and URLs

### URL mode (single URL):
Proceed directly to Step 2 with this single URL. No merchant lookup needed yet.

### REG_ID mode:
**1a. Find merchant by registration ID:**
```sql
SELECT merchant_hash_id, merchant_legal_name, merchant_registration_id, psp
FROM prod_agg_worldpay.gold.dim_merchants
WHERE merchant_registration_id = '<input>'
```

If no results, try a LIKE search: `WHERE merchant_registration_id LIKE '%<input>%'`

If still no results, tell the user no merchant was found and stop.

If multiple merchants found, list them all and ask the user which one to assess. Once confirmed, continue.

**1b. Get all URLs for the merchant:**
```sql
SELECT website_domain
FROM prod_agg_worldpay.gold.dim_merchant_websites
WHERE merchant_hash_id = '<merchant_hash_id>'
```

If no URLs found, tell the user: "Merchant found (<name>) but no website URLs on file. Cannot run website assessment." and stop.

Note the merchant_hash_id, merchant_legal_name, merchant_registration_id, and psp for the report header.

### NAME mode:
**1a. Find merchant by name:**
```sql
SELECT merchant_hash_id, merchant_legal_name, merchant_registration_id, psp
FROM prod_agg_worldpay.gold.dim_merchants
WHERE LOWER(merchant_legal_name) LIKE LOWER('%<input>%')
LIMIT 10
```

If no results, try shorter substrings or individual words from the name.

If multiple merchants found, list them all with reg ID and PSP, and ask the user which one to assess.

If exactly one match, confirm the name with the user and proceed.

**1b. Get all URLs** (same query as REG_ID mode above).

---

## Step 2 — For each URL: Fetch website + find merchant in DB

For **URL mode**, process the single URL.
For **REG_ID/NAME mode**, process ALL URLs from Step 1b. Run URLs in parallel where possible (batch the fetches, reuse the same enrichment data since it's per-merchant not per-URL).

### 2a. Fetch markdown for each URL via bash:

For a single URL:
```bash
BASEDIR="/tmp/merchantmodel"
URL="<the_url>"
case "$URL" in http://*|https://*) ;; *) URL="https://$URL" ;; esac
DOMAIN=$(echo "$URL" | sed -E 's|https?://||' | sed 's|/.*||')
DOMAIN_SAFE=$(echo "$DOMAIN" | sed 's/[^a-zA-Z0-9]/_/g')
mkdir -p "${BASEDIR}_${DOMAIN_SAFE}"
curl -s --max-time 30 \
  -H "Authorization: Bearer $(python3 -c "exec(open('$HOME/Documents/Claude code/credentials.py').read()); print(JINA_TOKEN)")" \
  "https://r.jina.ai/${URL}" \
  -o "${BASEDIR}_${DOMAIN_SAFE}/markdown.md"
wc -c < "${BASEDIR}_${DOMAIN_SAFE}/markdown.md"
echo "DOMAIN=$DOMAIN"
echo "DOMAIN_SAFE=$DOMAIN_SAFE"
echo "URL=$URL"
```

If result is 0 bytes and domain has no `www.`, retry with `www.` prepended. If still empty, write `Title: Error\n\nPage not found` to the markdown file and continue.

**For multiple URLs (REG_ID/NAME mode):** Fetch all URLs in parallel using a single bash command:
```bash
BASEDIR="/tmp/merchantmodel"
for RAW_URL in "domain1.com" "domain2.com" "domain3.com"; do
  URL="https://$RAW_URL"
  DOMAIN=$(echo "$URL" | sed -E 's|https?://||' | sed 's|/.*||')
  DOMAIN_SAFE=$(echo "$DOMAIN" | sed 's/[^a-zA-Z0-9]/_/g')
  mkdir -p "${BASEDIR}_${DOMAIN_SAFE}"
  curl -s --max-time 30 \
    -H "Authorization: Bearer $(python3 -c "exec(open('$HOME/Documents/Claude code/credentials.py').read()); print(JINA_TOKEN)")" \
    "https://r.jina.ai/${URL}" \
    -o "${BASEDIR}_${DOMAIN_SAFE}/markdown.md" &
done
wait
# Check sizes
for RAW_URL in "domain1.com" "domain2.com" "domain3.com"; do
  DOMAIN_SAFE=$(echo "$RAW_URL" | sed 's/[^a-zA-Z0-9]/_/g')
  SIZE=$(wc -c < "${BASEDIR}_${DOMAIN_SAFE}/markdown.md")
  echo "$RAW_URL: ${SIZE} bytes"
done
```

If any URL returns 0 bytes and has no `www.`, retry that specific URL with `www.` prepended.

### 2b. Domain lookup in DB

**URL mode only** (for REG_ID/NAME mode, you already have the merchant_hash_id from Step 1):
```sql
SELECT website_domain, merchant_hash_id
FROM prod_agg_worldpay.gold.dim_merchant_websites
WHERE website_domain IN ('<domain>', '<domain_without_www>', 'www.<domain_without_www>')
LIMIT 1
```

---

## Step 3 — Get enrichment data (once per merchant, not per URL)

Use the `merchant_hash_id` (from Step 1 for REG_ID/NAME mode, or Step 2b for URL mode). **Call all 5 SQL queries simultaneously:**

```sql
-- Q1: Transaction metrics (HAS date_day) — fetches rate AND transaction count for floor logic + ratio
SELECT transaction_count_28d, dispute_rate_28d FROM prod_agg_worldpay.gold.fct_merchant_metrics WHERE merchant_hash_id = '<ID>' ORDER BY date_day DESC LIMIT 1

-- Q2: IP bad percentage (NO date_day)
SELECT ip_count_bad_pct FROM prod_agg_worldpay.gold.fct_risk_website_details WHERE merchant_hash_id = '<ID>' LIMIT 1

-- Q3: Web Traffic — visit count only (HAS date_day)
SELECT visit_count_monthly as visit_count FROM prod_agg_worldpay.gold.fct_risk_website_traffic WHERE merchant_hash_id = '<ID>' ORDER BY date_day DESC LIMIT 1

-- Q4: Business Detail Components — contact_email + mcc_match only (NO date_day)
SELECT contact_email_risk_points, mcc_match_risk_points FROM prod_agg_worldpay.gold.fct_risk_website_business_details WHERE merchant_hash_id = '<ID>' LIMIT 1

-- Q5: Website age (HAS date_day)
SELECT website_age_days FROM prod_agg_worldpay.gold.fct_merchant_fraud_metrics WHERE merchant_hash_id = '<ID>' ORDER BY date_day DESC LIMIT 1
```

If no merchant_hash_id was found (URL mode with no DB match), skip this step entirely.

---

## Step 4 — Run prediction(s)

### Single URL (URL mode):
```bash
python3 "/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/Claude/predict_merchant.py" \
  "/tmp/merchantmodel_<DOMAIN_SAFE>/markdown.md" "<URL>" \
  --db-dispute-rate <val> \
  --transaction-count <val> \
  --ip-count-bad-pct <val> \
  --visit-count <val> \
  --website-age-days <val> \
  --contact-email-risk <val> \
  --mcc-match-risk <val>
```

### Multiple URLs (REG_ID/NAME mode):
Run predict_merchant.py for EACH URL, using the **same enrichment flags** for all (enrichment is per-merchant). You can run these in parallel using background processes:

```bash
for RAW_URL in "domain1.com" "domain2.com" "domain3.com"; do
  URL="https://$RAW_URL"
  DOMAIN_SAFE=$(echo "$RAW_URL" | sed 's/[^a-zA-Z0-9]/_/g')
  python3 "/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/Claude/predict_merchant.py" \
    "/tmp/merchantmodel_${DOMAIN_SAFE}/markdown.md" "$URL" \
    --db-dispute-rate <val> \
    --transaction-count <val> \
    --ip-count-bad-pct <val> \
    --visit-count <val> \
    --website-age-days <val> \
    --contact-email-risk <val> \
    --mcc-match-risk <val>
  echo "---URL_SEPARATOR---"
done
```

**Important:** Always pass both `--db-dispute-rate` AND `--transaction-count` together. The model automatically floors the rate (uses neutral default) when transaction count < 100. The transaction count and visit count are also used to compute the txn/visit ratio feature internally.

**Omit** any flag where the value is null or that query returned no rows.

---

## Step 5 — Present the AutoFaizal report

Output a structured report. **This is the most important step — take care to make the output clear, insightful, and useful.**

### Risk categories (use these labels based on the probability score):

| Probability | Category | Description |
|-------------|----------|-------------|
| >50% | EXTREMELY SUSPICIOUS | Almost certainly fraudulent — immediate action recommended |
| >35% | VERY SUSPICIOUS | Strong indicators of fraud — requires urgent review |
| >25% | PROBABLY SUSPICIOUS | Multiple risk signals present — should be investigated |
| >15% | POSSIBLY SUSPICIOUS | Some concern — worth a closer look |
| >8% | ELEVATED | Minor signals detected — monitor but low priority |
| <=8% | NOT SUSPICIOUS | No significant risk indicators found |

### Format — Single URL (URL mode):

```
═══════════════════════════════════════════════════════════════
  AutoFaizal — Merchant Risk Assessment
═══════════════════════════════════════════════════════════════

  URL:          <url>
  Domain:       <domain>
  Score:        <probability as %>
  Risk Level:   <RISK CATEGORY from table above>

───────────────────────────────────────────────────────────────
  KEY PREDICTORS
───────────────────────────────────────────────────────────────

  Show a table of the most impactful features. For each predictor show:
  - The feature name (human-readable)
  - The value
  - A signal indicator: 🔴 (high risk), 🟡 (moderate), 🟢 (low risk)
  - A brief note explaining why it matters

  Only include features that meaningfully contribute to the prediction.
  Group them: Website Signals first, then Internal Data signals.

  WEBSITE SIGNALS:
  | Predictor           | Value | Signal | Note                              |
  |---------------------|-------|--------|-----------------------------------|
  | Website Risk Score  | 1/5   | 🟢     | Site appears well-constructed     |
  | SKU Count           | 3/3   | 🟢     | Large product catalogue            |
  | Compliance Score    | 3/3   | 🟢     | Privacy, terms, and refund policy |
  | Has Social Media    | Yes   | 🟢     | Social media links present        |
  | ...                 |       |        |                                   |

  INTERNAL DATA SIGNALS (only if Databricks data found):
  | Predictor              | Value      | Signal | Note                           |
  |------------------------|------------|--------|--------------------------------|
  | IP Bad Percentage      | 1.3%       | 🟢     | Very low malicious IP rate     |
  | Txn/Visit Ratio        | 0.15       | 🟢     | Normal txn volume vs traffic   |
  | ...                    |            |        |                                |

───────────────────────────────────────────────────────────────
  COMMENTARY
───────────────────────────────────────────────────────────────

  Write 2-4 sentences of analyst-style commentary (see tone guide below).

───────────────────────────────────────────────────────────────
  DATA SOURCES
───────────────────────────────────────────────────────────────

  List which of the 5 enrichment sources returned data, and which didn't:
  ✅ Transaction Metrics  ❌ IP Data  ✅ Web Traffic  etc.

  Or: "No Databricks match — score based on website content only"

═══════════════════════════════════════════════════════════════
```

### Format — Multi-URL (REG_ID/NAME mode):

```
═══════════════════════════════════════════════════════════════
  AutoFaizal — Merchant Risk Assessment
═══════════════════════════════════════════════════════════════

  Merchant:     <merchant_legal_name>
  Reg ID:       <merchant_registration_id>
  PayFac:       <psp>
  URLs Assessed: <N>

───────────────────────────────────────────────────────────────
  OVERALL MERCHANT SCORE
───────────────────────────────────────────────────────────────

  Average Score:     <mean probability across all URLs, as %>
  Highest Score:     <max probability, as %>
  Risk Level:        <RISK CATEGORY based on AVERAGE score>
  URLs Flagged:      <count where prob > 15%> / <total> (<percentage>%)

───────────────────────────────────────────────────────────────
  PER-URL BREAKDOWN
───────────────────────────────────────────────────────────────

  | # | Domain                    | Score  | Risk Level          | Key Signal       |
  |---|---------------------------|--------|---------------------|------------------|
  | 1 | example.com               | 8.2%   | ELEVATED            | Low content      |
  | 2 | shop.example.com          | 4.1%   | NOT SUSPICIOUS      | —                |
  | 3 | promo-example.online      | 32.5%  | PROBABLY SUSPICIOUS | Error page       |

  Sort by score descending (highest risk first).
  "Key Signal" = the single most notable feature for that URL (or "—" if clean).

───────────────────────────────────────────────────────────────
  KEY PREDICTORS (MERCHANT-LEVEL)
───────────────────────────────────────────────────────────────

  Show the enrichment data (shared across all URLs) and the most common
  website-level signals.

  INTERNAL DATA SIGNALS (same for all URLs):
  | Predictor              | Value      | Signal | Note                           |
  |------------------------|------------|--------|--------------------------------|
  | IP Bad Percentage      | 1.3%       | 🟢     | Very low malicious IP rate     |
  | Txn/Visit Ratio        | 0.15       | 🟢     | Normal txn volume vs traffic   |
  | ...                    |            |        |                                |

  WEBSITE SIGNALS (summary across URLs):
  | Signal                  | URLs with concern | Note                          |
  |-------------------------|-------------------|-------------------------------|
  | Error pages             | 1 / 3             | promo-example.online is down  |
  | No compliance info      | 0 / 3             | All sites have policies       |
  | Generic email           | 0 / 3             | —                             |

───────────────────────────────────────────────────────────────
  COMMENTARY
───────────────────────────────────────────────────────────────

  Write 2-4 sentences of analyst-style commentary covering the overall
  merchant picture. Reference specific URLs if they stand out. Note whether
  the risk is concentrated in one URL or spread across many.

───────────────────────────────────────────────────────────────
  DATA SOURCES
───────────────────────────────────────────────────────────────

  ✅/❌ for each enrichment source + "Assessed N URLs via website content"

═══════════════════════════════════════════════════════════════
  Model: AutoFaizal v12 — Two-model blend (GBM + sigmoid)
  16 features, is_app_store capped at ~15% influence
  Includes txn/visit ratio for volume-vs-traffic analysis
  Trained on 539 merchants (101 original + 438 crawled)
═══════════════════════════════════════════════════════════════
```

### Commentary tone guide (match to risk level):

- **EXTREMELY SUSPICIOUS (>50%):** Lead with the strongest red flags. State clearly this merchant should be blocked or immediately reviewed. Reference the specific values driving the score.
- **VERY SUSPICIOUS (>35%):** Highlight the key risk signals. Recommend urgent review. Note any mitigating factors but emphasise the overall concern.
- **PROBABLY SUSPICIOUS (>25%):** Present a balanced but concerned assessment. Flag the specific features above the risk thresholds. Recommend investigation.
- **POSSIBLY SUSPICIOUS (>15%):** Note the mild risk signals. Suggest monitoring or light-touch review. Acknowledge that the merchant may be legitimate.
- **ELEVATED (>8%):** Brief note on what's slightly unusual. No action needed but worth awareness. Most merchants in this band are legitimate.
- **NOT SUSPICIOUS (<=8%):** Confirm the merchant looks clean. Briefly note which signals are reassuring. No review needed.

Be specific — reference actual values. Write as if briefing a fraud analyst.

For multi-URL assessments, also comment on the spread: Are all URLs similar risk, or is one outlier dragging the score up? Is there a pattern (e.g., all promotional .online domains are riskier)?

### Signal thresholds (guidelines, use judgement):

**Website signals:**
- risk_score: 1-2 🟢, 3 🟡, 4-5 🔴
- sku_count: 2-3 🟢, 1 🟡, 0 🔴
- compliance_score: 3 🟢, 2 🟡, 0-1 🔴
- has_social_media: Yes 🟢, No 🟡
- page_is_error: No 🟢, Yes 🔴
- is_email_generic: No 🟢, Yes 🔴

**Internal data signals:**
- ip_count_bad_pct: <10 🟢, 10-40 🟡, >40 🔴
- dispute_rate_28d: <0.005 🟢, 0.005-0.02 🟡, >0.02 🔴 (only when txn count >= 100)
- txn_visit_ratio: <1 🟢, 1-5 🟡, >5 🔴 (txn count / visit count)
- contact_email_risk: 0 🟢, 1 🟡 (missing), 3 🔴 (risky)
- mcc_match_risk: 0 🟢, 1 🟡 (non-risky mismatch), 3 🔴 (risky mismatch)
- website_age_days: >730 🟢, 365-730 🟡, <365 🔴
- visit_count: >100000 🟢, 10000-100000 🟡, <10000 🔴