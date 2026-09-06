#!/usr/bin/env bash
# Einrichtung in einem Rutsch. Idempotent — kann wiederholt laufen.
#
#   ./setup.sh
#
# Legt ein venv an, installiert alles, prueft die Feeds (Bauschritt 1) und
# sagt am Ende, was noch fehlt.
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Python-Umgebung"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  echo "    .venv angelegt"
else
  echo "    .venv existiert bereits"
fi
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e ".[dev]"
echo "    Abhaengigkeiten installiert"

echo
echo "==> Konfiguration"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    .env aus .env.example angelegt — Schluessel dort eintragen"
else
  echo "    .env existiert bereits"
fi

echo
echo "==> Tests"
.venv/bin/python -m pytest -q

echo
echo "==> Bauschritt 1: Feed-URLs verifizieren"
echo "    (dauert ein bis zwei Minuten, ruft jede Quelle einmal ab)"
.venv/bin/mi verify-sources

echo
echo "==> Vorflugkontrolle"
.venv/bin/mi preflight || true

echo
echo "Naechste Schritte:"
echo "  1. sources.lock.yaml durchsehen. Bei 'no_feed' den html.item_selector"
echo "     in sources.yaml gegen die echte Seite pruefen."
echo "  2. .env ausfuellen (ANTHROPIC_API_KEY, MI_MAIL_*)."
echo "  3. Drei Tage sammeln:  .venv/bin/mi collect && .venv/bin/mi prefilter"
echo "  4. Dann kalibrieren:   .venv/bin/mi calibrate"
echo "  5. sources.lock.yaml committen, Secrets im Repo setzen — erst dann"
echo "     laufen die GitHub-Actions-Workflows durch."
