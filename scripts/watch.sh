#!/usr/bin/env bash
# Book shelf watcher — runs scan + sync every 5 minutes
# Usage: ./watch.sh [--once] [--interval 300]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
INTERVAL="${2:-300}"
RUN_ONCE="${1:-}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

run_cycle() {
    log "Scanning vault..."
    python3 "$SCRIPT_DIR/watcher.py" \
        --vault ~/obsidian \
        --out "$REPO_DIR" \
        --covers "$REPO_DIR/covers" || log "ERROR: scan failed"

    log "Syncing to S3..."
    python3 "$SCRIPT_DIR/sync_s3.py" \
        --local "$REPO_DIR" \
        --bucket tb1/books || log "ERROR: s3 sync failed"

    log "Git commit + push..."
    cd "$REPO_DIR"
    git add books.json covers/ index.html app.js styles.css 2>/dev/null || true
    if ! git diff --cached --quiet 2>/dev/null; then
        git commit -m "auto: book shelf update $(date '+%Y-%m-%d %H:%M')" 2>/dev/null || true
        git push origin main 2>/dev/null || log "WARN: git push failed (may need auth)"
        log "Pushed to GitHub"
    else
        log "No changes to commit"
    fi
}

# Run once if requested
if [[ "$RUN_ONCE" == "--once" ]]; then
    run_cycle
    exit 0
fi

# Continuous loop
log "Starting book shelf watcher (interval: ${INTERVAL}s)"
while true; do
    run_cycle
    log "Sleeping ${INTERVAL}s..."
    sleep "$INTERVAL"
done