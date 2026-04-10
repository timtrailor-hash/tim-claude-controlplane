---
name: markdown_modeloptimised
description: "Fetch website markdown via Jina API. Optimised for the ML model pipeline -- saves to /tmp for speed, no Google Drive overhead."
user-invocable: true
---
# Markdown (Model Optimised)

Fetch website markdown via Jina API. Optimised for the ML model pipeline — saves to /tmp for speed, no Google Drive overhead.

## Arguments
- `$ARGUMENTS` - The website URL to fetch (required)

## Instructions

1. Validate that a URL was provided in `$ARGUMENTS`. If none, ask the user.

2. Fetch the markdown:

```bash
URL="$ARGUMENTS"
case "$URL" in http://*|https://*) ;; *) URL="https://$URL" ;; esac
DOMAIN=$(echo "$URL" | sed -E 's|https?://||' | sed 's|/.*||' | sed 's/[^a-zA-Z0-9]/_/g')
TMPDIR="/tmp/merchantmodel_${DOMAIN}"
mkdir -p "$TMPDIR"
curl -s --max-time 30 -H "Authorization: Bearer $(python3 -c "exec(open('$HOME/Documents/Claude code/credentials.py').read()); print(JINA_TOKEN)")" \
  "https://r.jina.ai/${URL}" -o "$TMPDIR/markdown.md"
echo "Saved: $TMPDIR/markdown.md"
echo "URL: $URL"
echo "Domain: $DOMAIN"
```

3. Confirm the file was saved and report the path.

## Output
- Markdown file: `/tmp/merchantmodel_{domain}/markdown.md`

## Example
```
/markdown_modeloptimised www.example.com
```