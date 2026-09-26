#!/usr/bin/env bash
# Rotate launchd stdout/stderr logs of mAIl (they grow without bound; launchd never rotates them).
# Copy-truncate: gzip a copy into logs/archive, then truncate in place. launchd opens these files
# with O_APPEND, so the running jobs keep writing to the (now empty) file. No sudo needed.
# Also removes nightly report JSONs (tag-migration-*.json) older than 30 days.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS="$ROOT/logs"
ARCH="$LOGS/archive"
MAX_BYTES="${MAX_BYTES:-20971520}"   # 20 MB
KEEP="${KEEP:-5}"
umask 077
mkdir -p "$ARCH"
stamp="$(date +%Y%m%d-%H%M%S)"
rotated=0
for f in "$LOGS"/launchd-*.log; do
  [ -f "$f" ] || continue
  size=$(stat -f %z "$f")
  [ "$size" -gt "$MAX_BYTES" ] || continue
  base="$(basename "$f")"
  gzip -c "$f" > "$ARCH/$base.$stamp.gz"
  gzip -t "$ARCH/$base.$stamp.gz"
  : > "$f"
  rotated=$((rotated + 1))
  # keep the newest $KEEP archives for this log
  ls -1t "$ARCH/$base".*.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do rm -f "$old"; done
done
old_reports=$(find "$LOGS" -maxdepth 1 -type f -name 'tag-migration-*.json' -mtime +30 | wc -l | tr -d ' ')
find "$LOGS" -maxdepth 1 -type f -name 'tag-migration-*.json' -mtime +30 -delete
echo "$(date -u +%FT%TZ) rotated=$rotated removed_old_reports=$old_reports"
