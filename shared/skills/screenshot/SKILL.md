---
name: screenshot
description: "Take a screenshot of a website using headless Chrome and save it to Google Drive."
user-invocable: true
disable-model-invocation: true
---
# Screenshot Command

Take a screenshot of a website using headless Chrome and save it to Google Drive.

Follow the instructions in the reference file:
`/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/Screenshots/Claude Instructions/screenshot_instructions_mac.md`

## Arguments
- `$ARGUMENTS` - The website URL to capture (required)

## Instructions

When this command is invoked, execute the following steps:

1. Validate that a URL was provided in `$ARGUMENTS`. If no URL is provided, ask the user for a website URL.

2. Run the full-page screenshot command (1920x8000, scrollbars hidden):

```bash
URL="$ARGUMENTS"
case "$URL" in http://*|https://*) ;; *) URL="https://$URL" ;; esac
FILENAME=$(echo "$URL" | sed -E 's|https?://||' | sed 's|/.*||' | sed 's/[^a-zA-Z0-9]/_/g')
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
OUTDIR="/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/outputs/${FILENAME}_${TIMESTAMP}"
mkdir -p "$OUTDIR"
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --hide-scrollbars --screenshot="${OUTDIR}/${FILENAME}_${TIMESTAMP}_screenshot.png" --window-size=1920,8000 "$URL"
```

3. Confirm to the user that the screenshot was saved, including the filename and location.

## Output Location
Screenshots are saved to a subfolder in:
`/Users/timtrailor/Library/CloudStorage/GoogleDrive-tim.trailor@envisso.com/My Drive/Hackathon/outputs/{domain}_{YYYYMMDD_HHMMSS}/`

## Example Usage
```
/screenshot https://example.com
```
This will save the screenshot as `example_com_20260211_143000_screenshot.png` inside `Hackathon/outputs/example_com_20260211_143000/`.