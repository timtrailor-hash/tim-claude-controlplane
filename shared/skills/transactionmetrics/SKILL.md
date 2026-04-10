---
name: transactionmetrics
description: "Analyze transaction and dispute data for a website by querying Databricks, using the URL from a markdown agent output."
user-invocable: true
---
# Transaction Metrics Command

Analyze transaction and dispute data for a website by querying Databricks, using the URL from a markdown agent output.

Follow the instructions in the reference file:
`/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/Claude/Claude instructions/Transaction Metrics Prompt.md`

## Arguments
- `$ARGUMENTS` - The folder name inside `Hackathon/outputs/` (e.g. `www_snowcirculate_com_20260211_150124`), a full path to a markdown file, or leave empty to use the most recent output folder.

## Instructions

When this command is invoked, execute the following steps:

1. **Locate the markdown file:**
   - If `$ARGUMENTS` is a full path to a `.md` file, use it directly.
   - If `$ARGUMENTS` is a folder name, look for the `_markdown.md` file inside `/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/outputs/$ARGUMENTS/`.
   - If no argument is provided, find the most recently created folder in `Hackathon/outputs/` and use the markdown file inside it.

2. **Extract the website URL** from the markdown file. The URL is on the `URL Source:` line near the top of the file.

3. **Extract the domain** from the URL (e.g. `www.snowcirculate.com` or `snowcirculate.com`). Handle both formats: with or without `https://`.

4. **Query Databricks** for the following metrics using the `execute_sql` or `execute_sql_read_only` tools:

   **A. Transaction Metrics** from `prod_agg_worldpay.gold.fct_transactions`:
   - Most occurring transaction amount (mode) in original and PSP currency
   - Average transaction amount in original and PSP currency
   - Median transaction amount in original and PSP currency
   - Total processing volume (sum of PSP currency amounts)
   - Transaction count

   **B. Website Age** from `prod_agg_worldpay.gold.fct_risk_website_details`:
   - Domain registration date
   - Website age in days and years

   **C. Dispute Metrics** from `prod_agg_worldpay.gold.fct_disputes`:
   - Most occurring dispute amount (mode) in original and PSP currency
   - Average dispute amount in original and PSP currency
   - Median dispute amount in original and PSP currency
   - Dispute count
   - Dispute rate (disputes/transactions * 100)

5. **Save the output** as a `.json` file in the **same folder** as the markdown file. Use the same naming convention but with `_transaction_metrics.json` suffix.
   - Example: if the folder contains `www_snowcirculate_com_20260211_150124_markdown.md`, save as `www_snowcirculate_com_20260211_150124_transaction_metrics.json`

6. The JSON output should have this structure:
```json
{
  "website_url": "<url>",
  "domain": "<domain>",
  "transaction_metrics": {
    "transaction_count": null,
    "total_processing_volume": null,
    "total_processing_volume_currency": null,
    "mode_transaction_amount": null,
    "mode_transaction_amount_psp": null,
    "avg_transaction_amount": null,
    "avg_transaction_amount_psp": null,
    "median_transaction_amount": null,
    "median_transaction_amount_psp": null
  },
  "website_age": {
    "domain_registration_date": null,
    "website_age_days": null,
    "website_age_years": null
  },
  "dispute_metrics": {
    "dispute_count": null,
    "mode_dispute_amount": null,
    "mode_dispute_amount_psp": null,
    "avg_dispute_amount": null,
    "avg_dispute_amount_psp": null,
    "median_dispute_amount": null,
    "median_dispute_amount_psp": null,
    "dispute_rate_percent": null
  }
}
```

7. Use `null` for any metrics that return no data. The output file must contain ONLY valid JSON.

8. Confirm to the user that the metrics were saved, including the filename, location, and a summary of key findings.

## Important Notes
- Always use `DESCRIBE TABLE` before writing queries to confirm column names
- Handle NULL values appropriately
- Use `LIKE` matching on domain as well as exact match to maximise results
- If MODE() is not supported, use GROUP BY with ORDER BY count DESC LIMIT 1

## Output Location
Transaction metrics JSON is saved in the same folder as the markdown file:
`/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/outputs/{domain}_{YYYYMMDD_HHMMSS}/`

## Example Usage
```
/transactionmetrics www_snowcirculate_com_20260211_150124
```
Or with no argument (uses the most recent output):
```
/transactionmetrics
```