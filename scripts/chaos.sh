#!/usr/bin/env bash
# 장애 주입 도구 (AIOps 데모용)
#   ./scripts/chaos.sh error [rate]      저장소 I/O 오류 주입 (기본 rate=0.5)
#   ./scripts/chaos.sh slow [ms]         저장소 지연 주입 (기본 800ms)
#   ./scripts/chaos.sh down              앱 컨테이너 중지
#   ./scripts/chaos.sh off               장애 해제 (중지됐다면 다시 시작)
#   ./scripts/chaos.sh status            현재 장애 주입 상태
set -euo pipefail
cd "$(dirname "$0")/.."

APP_URL="${APP_URL:-http://localhost:5001}"
TOKEN="${CHAOS_ADMIN_TOKEN:-$(grep -E '^CHAOS_ADMIN_TOKEN=' .env 2>/dev/null | cut -d= -f2-)}"
[[ -n "$TOKEN" ]] || { echo "CHAOS_ADMIN_TOKEN이 없습니다 (.env 확인)" >&2; exit 1; }

put() { curl -fsS -X PUT "$APP_URL/admin/chaos" -H "X-Admin-Token: $TOKEN" -H 'Content-Type: application/json' -d "$1"; echo; }

case "${1:-status}" in
  error)  put "{\"mode\":\"storage_error\",\"error_rate\":${2:-0.5}}" ;;
  slow)   put "{\"mode\":\"slow_storage\",\"latency_ms\":${2:-800}}" ;;
  down)   docker compose stop fastapi-app ;;
  off)    docker compose start fastapi-app >/dev/null; sleep 1; put '{"mode":"off"}' ;;
  status) curl -fsS "$APP_URL/admin/chaos" -H "X-Admin-Token: $TOKEN"; echo ;;
  *)      sed -n '2,8p' "$0"; exit 1 ;;
esac
