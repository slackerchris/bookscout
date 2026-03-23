#!/usr/bin/env bash
# BookScout — qBittorrent post-download hook
#
# Calls BookScout's import API when a torrent finishes downloading so it can
# extract archives and organise files into <library_root>/<Author>/<Series>/<Title>/.
#
# SETUP
# -----
# 1. Set postprocess.mode: bookscout and postprocess.library_root in config.yaml
# 2. In qBittorrent: Settings → Downloads → "Run external program on torrent completion"
#    Paste this (adjust path to wherever you saved this script):
#
#      /opt/scripts/qbittorrent-postprocess.sh "%N" "%L" "%G" "%F"
#
#    %N = torrent name
#    %L = category / label
#    %G = tags  (BookScout stamps "bookscout-{book_id}" here automatically)
#    %F = content path (file or root folder of the torrent)
#
# 3. Make the script executable:  chmod +x /opt/scripts/qbittorrent-postprocess.sh
#
# 4. Set BOOKSCOUT_URL below (or export it as an environment variable).
#
# FILTERING
# ---------
# Set TRIGGER_CATEGORY to only fire for a specific qBittorrent category
# (e.g. "audiobooks").  Leave empty ("") to trigger for ALL completed torrents.
# ---------------------------------------------------------------------------

set -euo pipefail

TORRENT_NAME="${1:-}"
CATEGORY="${2:-}"
TAGS="${3:-}"
CONTENT_PATH="${4:-}"

BOOKSCOUT_URL="${BOOKSCOUT_URL:-http://localhost:8765}"
TRIGGER_CATEGORY="${TRIGGER_CATEGORY:-audiobooks}"

# Optional: path to a log file (comment out to disable)
LOG_FILE="${LOG_FILE:-/var/log/bookscout-import.log}"

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] bookscout-import: $*"
    echo "$msg"
    [[ -n "$LOG_FILE" ]] && echo "$msg" >> "$LOG_FILE"
}

# ── Category filter ──────────────────────────────────────────────────────────
if [[ -n "$TRIGGER_CATEGORY" && "$CATEGORY" != "$TRIGGER_CATEGORY" ]]; then
    log "Skipping '$TORRENT_NAME' (category='$CATEGORY', want='$TRIGGER_CATEGORY')"
    exit 0
fi

# ── Extract book_id from tags ────────────────────────────────────────────────
# BookScout adds "bookscout-{id}" when submitting the torrent.
# Tags from qBittorrent are comma-separated, e.g. "bookscout-42,audiobooks"
BOOK_ID=$(echo "$TAGS" | grep -oP 'bookscout-\K[0-9]+' || true)

if [[ -z "$BOOK_ID" ]]; then
    log "WARNING: No bookscout tag found for '$TORRENT_NAME' (tags='$TAGS') — skipping."
    log "         Make sure you submitted this torrent via BookScout's download API."
    exit 0
fi

log "Importing book_id=$BOOK_ID name='$TORRENT_NAME' path='$CONTENT_PATH'"

RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST "$BOOKSCOUT_URL/api/v1/books/$BOOK_ID/import" \
    -H "Content-Type: application/json" \
    -d "{\"source_path\": $(printf '%s' "$CONTENT_PATH" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}")

HTTP_BODY=$(echo "$RESPONSE" | head -n -1)
HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)

if [[ "$HTTP_CODE" == "200" ]]; then
    JOB_ID=$(echo "$HTTP_BODY" | grep -oP '"job_id"\s*:\s*"\K[^"]+' || true)
    log "Import queued OK — job_id=$JOB_ID"
else
    log "ERROR: BookScout returned HTTP $HTTP_CODE — $HTTP_BODY"
    exit 1
fi
