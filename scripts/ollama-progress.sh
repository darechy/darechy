#!/usr/bin/env bash
# Прогресс скачивания моделей Ollama: сколько уже лежит на диске и что готово.
# Использование: ./scripts/ollama-progress.sh [интервал_сек]
set -u

BLOBS="$HOME/.ollama/models/blobs"
INTERVAL="${1:-5}"

human() { awk -v b="$1" 'BEGIN { printf "%.2f GB", b/1073741824 }'; }

while true; do
  total=$(find "$BLOBS" -type f -exec stat -f %z {} + 2>/dev/null | awk '{s+=$1} END {print s+0}')
  partial=$(find "$BLOBS" -name '*-partial' -exec stat -f %z {} + 2>/dev/null | awk '{s+=$1} END {print s+0}')
  ready=$(ollama list 2>/dev/null | tail -n +2 | awk 'NF {print "    • " $1 "  (" $3 $4 ")"}')

  printf '\r\033[Kскачано всего: %s   (в незавершённых загрузках: %s)' \
    "$(human "$total")" "$(human "$partial")"

  if [[ -n "$ready" ]]; then
    printf '\n\033[Kготовые модели:\n%s\n\033[1A' "$ready"
  fi

  sleep "$INTERVAL"
done
