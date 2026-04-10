---
name: MD2Features_modeloptimised
description: "Extract only the features needed by the ML model from a website's markdown content. Uses deterministic heuristic extraction (no LLM analysis) for speed and consistency."
user-invocable: true
---
# MD2Features (Model Optimised)

Extract only the features needed by the ML model from a website's markdown content. Uses deterministic heuristic extraction (no LLM analysis) for speed and consistency.

The model requires these 5 website-derived features:
- `risk_score` (1-5)
- `language_quality` (1-5)
- `vars_compliance_enc` (0-3)
- `sku_count_enc` (0-3)
- `has_contact_details_enc` (0/1)

Plus `is_app_store` which is derived from the URL (not the markdown).

## Arguments
- `$ARGUMENTS` - Either:
  - A domain name (e.g. `www.example.com`) — will look for `/tmp/merchantmodel_{domain}/markdown.md`
  - A full path to a markdown file

## Instructions

1. **Locate the markdown file:**
   - If `$ARGUMENTS` looks like a path ending in `.md`, use it directly.
   - Otherwise, treat it as a domain: sanitise it (`sed 's/[^a-zA-Z0-9]/_/g'`) and look for `/tmp/merchantmodel_{sanitised}/markdown.md`.

2. **Run the feature extraction** using the predict script's extract function:

```bash
MDFILE="<resolved_markdown_path>"
URL="<original_url_or_domain>"
python3 "/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/predict_merchant.py" \
  "$MDFILE" "$URL"
```

This outputs JSON with all 10 model features (the 3 DB features will default to 0 without `--db-*` flags).

3. **Save the features JSON** to the same directory as the markdown file, as `features_model.json`:

```bash
OUTDIR=$(dirname "$MDFILE")
python3 "/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/predict_merchant.py" \
  "$MDFILE" "$URL" > "$OUTDIR/features_model.json"
```

4. Confirm the file was saved and report the 5 website-derived feature values.

## Output
- Features JSON: `/tmp/merchantmodel_{domain}/features_model.json`
- Contains: `risk_score`, `language_quality`, `vars_compliance_enc`, `sku_count_enc`, `has_contact_details_enc`, `is_app_store`, plus DB fields (zeroed without DB data)

## Example
```
/MD2Features_modeloptimised www.example.com
```