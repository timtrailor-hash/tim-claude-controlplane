---
name: createoutput
description: "Merge the three JSON files (_features.json, _transaction_metrics.json, _risk_assessment.json) from an output folder into a single combined output JSON file."
user-invocable: true
disable-model-invocation: true
---
# Create Output Command

Merge the three JSON files (_features.json, _transaction_metrics.json, _risk_assessment.json) from an output folder into a single combined output JSON file.

## Arguments
- `$ARGUMENTS` - The folder name inside `Hackathon/outputs/` (e.g. `www_snowcirculate_com_20260211_154816`), or leave empty to use the most recent output folder.

## Instructions

When this command is invoked, execute the following steps:

1. **Locate the output folder:**
   - If `$ARGUMENTS` is a folder name, use `/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/outputs/$ARGUMENTS/`.
   - If no argument is provided, find the most recently created folder in `Hackathon/outputs/` and use that.

2. **Read all three JSON files** from the folder:
   - `{domain}_{TIMESTAMP}_features.json` (from MD2Features agent)
   - `{domain}_{TIMESTAMP}_transaction_metrics.json` (from transactionmetrics agent)
   - `{domain}_{TIMESTAMP}_risk_assessment.json` (from claudedirect2features agent)
   - If any file is missing, note it but continue with whatever files are available.

3. **Merge into a single JSON** with this structure:
```json
{
  "website_url": "<from transaction_metrics or extracted from folder name>",
  "domain": "<from transaction_metrics or extracted from folder name>",
  "timestamp": "<extracted from folder name>",
  "md2features": { <entire contents of _features.json> },
  "transaction_metrics": { <transaction_metrics object from _transaction_metrics.json> },
  "website_age": { <website_age object from _transaction_metrics.json> },
  "dispute_metrics": { <dispute_metrics object from _transaction_metrics.json> },
  "risk_assessment": { <entire contents of _risk_assessment.json> }
}
```

4. **Save the merged file** in the same folder as `{domain}_{TIMESTAMP}_output.json`. The file must contain ONLY valid JSON.

5. Confirm to the user: the filename, location, and which source files were merged.

## Output Location
The merged file is saved in the same folder:
`/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/outputs/{domain}_{YYYYMMDD_HHMMSS}/`

## Example Usage
```
/createoutput www_snowcirculate_com_20260211_154816
```
Or with no argument (uses the most recent output folder):
```
/createoutput
```