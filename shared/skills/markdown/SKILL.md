---
name: markdown
description: "Fetch a clean markdown version of a website using the Jina AI Reader API and save it to Google Drive."
user-invocable: true
---
# Markdown Command

Fetch a clean markdown version of a website using the Jina AI Reader API and save it to Google Drive.

Follow the instructions in the reference file:
`/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/Claude/Claude instructions/Website Markdown Prompt.md`

## Arguments
- `$ARGUMENTS` - The website URL to fetch (required)

## Instructions

When this command is invoked, execute the following steps:

1. Validate that a URL was provided in `$ARGUMENTS`. If no URL is provided, ask the user for a website URL.

2. Fetch the markdown content using the Jina AI Reader API:

```bash
URL="$ARGUMENTS"
case "$URL" in http://*|https://*) ;; *) URL="https://$URL" ;; esac
FILENAME=$(echo "$URL" | sed -E 's|https?://||' | sed 's|/.*||' | sed 's/[^a-zA-Z0-9]/_/g')
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
OUTDIR="/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/outputs/${FILENAME}_${TIMESTAMP}"
mkdir -p "$OUTDIR"
curl -s -H "Authorization: Bearer $(python3 -c "exec(open('$HOME/Documents/Claude code/credentials.py').read()); print(JINA_TOKEN)")" "https://r.jina.ai/${URL}" -o "${OUTDIR}/${FILENAME}_${TIMESTAMP}_markdown.md"
```

3. Confirm to the user that the markdown was saved, including the filename and location.

## Output Location
Markdown files are saved to a subfolder in:
`/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/outputs/{domain}_{YYYYMMDD_HHMMSS}/`

## Example Usage
```
/markdown www.example.com
```
This will save the markdown as `www_example_com_20260211_150000_markdown.md` inside `Hackathon/outputs/www_example_com_20260211_150000/`.