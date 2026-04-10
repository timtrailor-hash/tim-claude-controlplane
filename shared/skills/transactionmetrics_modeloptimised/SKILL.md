---
name: transactionmetrics_modeloptimised
description: "Query Databricks for all enrichment data needed by the v2 ML model."
user-invocable: true
---
# Transaction Metrics (Model Optimised v2)

Query Databricks for all enrichment data needed by the v2 ML model:
- Transaction metrics: `dispute_count_180d`, `dispute_rate_28d`, `value_at_risk`
- ScamAdviser: `scamadviser_score`, `ip_count_bad_pct`
- Web Traffic (SemRush): `website_traffic_risk_score`, `visit_count`
- Reviews: `negative_review_pct`, `fraud_indicators_pct`
- Business Details: `business_details_risk_score`
- Envisso Scores: `envisso_risk_score`, `envisso_fraud_risk_score`
- Fraud Metrics: `website_age_days`, `merchant_transacting_domains_28d`

## Arguments
- `$ARGUMENTS` - A website domain (e.g. `www.example.com`) or URL

## Instructions

1. **Extract the domain** from `$ARGUMENTS`:
   - Strip `https://` or `http://` prefix
   - Strip trailing `/` and path
   - Try both with and without `www.` prefix

2. **Query 1 — Find the merchant:**

```sql
SELECT website_domain, merchant_hash_id
FROM prod_agg_worldpay.gold.dim_merchant_websites
WHERE website_domain = '<domain>'
   OR website_domain = '<domain_without_www>'
   OR website_domain = '<domain_with_www>'
LIMIT 1
```

3. **If no match found:** Save a JSON with `has_db_data: false` and all metrics as null. Skip to step 5.

4. **If match found — Run all 6 enrichment queries in parallel** using the `merchant_hash_id`:

**Query 2 — Transaction metrics:**
```sql
SELECT dispute_count_180d, dispute_rate_28d, value_at_risk
FROM prod_agg_worldpay.gold.fct_merchant_metrics
WHERE merchant_hash_id = '<merchant_hash_id>'
ORDER BY date_day DESC LIMIT 1
```

**Query 3 — ScamAdviser:**
```sql
SELECT scamadviser_score, ip_count_bad_pct
FROM prod_agg_worldpay.gold.fct_risk_website_details
WHERE merchant_hash_id = '<merchant_hash_id>'
ORDER BY date_day DESC LIMIT 1
```

**Query 4 — Web Traffic:**
```sql
SELECT website_traffic_risk_score, visit_count_monthly as visit_count
FROM prod_agg_worldpay.gold.fct_risk_website_traffic
WHERE merchant_hash_id = '<merchant_hash_id>'
ORDER BY date_day DESC LIMIT 1
```

**Query 5 — Reviews:**
```sql
SELECT negative_review_pct, fraud_indicators_pct
FROM prod_agg_worldpay.gold.fct_risk_external_perception
WHERE merchant_hash_id = '<merchant_hash_id>'
ORDER BY date_day DESC LIMIT 1
```

**Query 6 — Business Details:**
```sql
SELECT business_details_risk_score
FROM prod_agg_worldpay.gold.fct_risk_website_business_details
WHERE merchant_hash_id = '<merchant_hash_id>'
ORDER BY date_day DESC LIMIT 1
```

**Query 7 — Envisso Scores:**
```sql
SELECT risk_score as envisso_risk_score, fraud_risk_score as envisso_fraud_risk_score
FROM prod_agg_worldpay.gold.fct_envisso_risk_scores_latest
WHERE merchant_hash_id = '<merchant_hash_id>'
LIMIT 1
```

**Query 8 — Fraud Metrics:**
```sql
SELECT website_age_days, merchant_transacting_domains_28d
FROM prod_agg_worldpay.gold.fct_merchant_fraud_metrics
WHERE merchant_hash_id = '<merchant_hash_id>'
ORDER BY date_day DESC LIMIT 1
```

5. **Save the output** as `db_metrics_model.json` in `/tmp/merchantmodel_{domain}/`:

```json
{
  "domain": "<domain>",
  "has_db_data": true,
  "merchant_hash_id": "<id>",
  "dispute_count_180d": <value or null>,
  "dispute_rate_28d": <value or null>,
  "value_at_risk": <value or null>,
  "scamadviser_score": <value or null>,
  "ip_count_bad_pct": <value or null>,
  "website_traffic_risk_score": <value or null>,
  "visit_count": <value or null>,
  "negative_review_pct": <value or null>,
  "fraud_indicators_pct": <value or null>,
  "business_details_risk_score": <value or null>,
  "envisso_risk_score": <value or null>,
  "envisso_fraud_risk_score": <value or null>,
  "website_age_days": <value or null>,
  "merchant_transacting_domains_28d": <value or null>
}
```

6. Confirm the result: whether the merchant was found in Databricks, and which data sources returned data.

## Output
- Metrics JSON: `/tmp/merchantmodel_{domain}/db_metrics_model.json`
- Max 8 SQL queries (1 lookup + 7 enrichment, run enrichment in parallel)

## Example
```
/transactionmetrics_modeloptimised www.example.com
```