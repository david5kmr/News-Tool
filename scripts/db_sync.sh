#!/usr/bin/env bash
# Archiv-DB ueber GitHub-Actions-Laeufe hinweg erhalten.
#
# Actions-Runner sind fluechtig — nach dem Job ist die SQLite-Datei weg. Der
# Archivzweck des Systems braucht sie aber dauerhaft. Deshalb liegt die DB in
# einem eigenen verwaisten Branch (Default: mi-data), der bei jedem Lauf mit
# genau einem Commit ueberschrieben wird. So bleibt das Repo klein: alte
# Blobs werden unerreichbar und von GitHub weggeraeumt.
#
#   scripts/db_sync.sh restore   # Branch auschecken, DB nach data/ legen
#   scripts/db_sync.sh save      # DB zurueckschreiben
#
# Alternativen, wenn die DB zu gross wird (Faustregel: ab ~200 MB):
#   - Litestream gegen S3/R2 (kontinuierliche Replikation)
#   - Turso/libSQL (verwaltetes SQLite)
#   - ein kleiner Server mit Cron statt Actions
set -euo pipefail

BRANCH="${MI_DATA_BRANCH:-mi-data}"
DB_PATH="${MI_DB_PATH:-data/mi.db}"
WORKTREE=".mi-data"

case "${1:-}" in
  restore)
    rm -rf "$WORKTREE"
    if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
      git fetch --depth 1 origin "$BRANCH"
      git worktree add --detach "$WORKTREE" "origin/$BRANCH" >/dev/null
      mkdir -p "$(dirname "$DB_PATH")"
      if [ -f "$WORKTREE/mi.db" ]; then
        cp "$WORKTREE/mi.db" "$DB_PATH"
        echo "Archiv wiederhergestellt: $(du -h "$DB_PATH" | cut -f1)"
      else
        echo "Branch $BRANCH existiert, enthaelt aber keine mi.db — starte leer"
      fi
      git worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
    else
      echo "Branch $BRANCH existiert noch nicht — erster Lauf, starte leer"
      mkdir -p "$(dirname "$DB_PATH")"
    fi
    ;;

  save)
    if [ ! -f "$DB_PATH" ]; then
      echo "Keine DB unter $DB_PATH — nichts zu sichern" >&2
      exit 0
    fi
    # WAL einfalten, sonst fehlen die letzten Schreibvorgaenge in der Kopie.
    python3 -c "
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
conn.close()
" "$DB_PATH"

    rm -rf "$WORKTREE"
    mkdir -p "$WORKTREE"
    cp "$DB_PATH" "$WORKTREE/mi.db"

    cd "$WORKTREE"
    git init -q -b "$BRANCH"
    git config user.name "market-intelligence-bot"
    git config user.email "actions@users.noreply.github.com"
    cat > README.md <<'EOF'
# mi-data

Archiv-Datenbank des Market-Intelligence-Systems. Dieser Branch wird bei
jedem Lauf ueberschrieben und traegt bewusst keine Historie — die Historie
steht in der Datenbank selbst.

Nicht mergen. Lokal holen mit:

    git fetch origin mi-data && git show origin/mi-data:mi.db > data/mi.db
EOF
    git add -A
    git commit -q -m "Archivstand $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    git push -q --force "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" \
      "HEAD:refs/heads/$BRANCH"
    cd ..
    rm -rf "$WORKTREE"
    echo "Archiv gesichert nach $BRANCH ($(du -h "$DB_PATH" | cut -f1))"
    ;;

  *)
    echo "Aufruf: $0 {restore|save}" >&2
    exit 2
    ;;
esac
