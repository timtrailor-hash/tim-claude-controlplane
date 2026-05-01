#!/bin/zsh
# Governors Streamlit daemon
export HOME="/Users/timtrailor"
export PYTHONPATH="$HOME/code:$PYTHONPATH"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

SRC="$HOME/code/ofsted-agent"
DST="$HOME/Library/Caches/governors_app"
mkdir -p "$DST"

# Sync required files to /tmp to avoid EINTR issues
for f in app.py shared_chat.py combined_context.md requirements.txt; do
    [ -f "$SRC/$f" ] && cp "$SRC/$f" "$DST/$f" 2>/dev/null
done
mkdir -p "$DST/.streamlit" 2>/dev/null
cp "$SRC/.streamlit/config.toml" "$DST/.streamlit/config.toml" 2>/dev/null || true

cd "$DST"
exec /opt/homebrew/bin/streamlit run app.py \
    --server.port 8501 \
    --server.headless true \
    --server.address 127.0.0.1 \
    --server.enableCORS false \
    --server.enableXsrfProtection false \
    --server.fileWatcherType none \
    --browser.gatherUsageStats false
