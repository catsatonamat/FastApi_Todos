#!/usr/bin/env bash
# 녹화 전 데모 상태 초기화: 메트릭·로그·감사 로그·인시던트·장애 설정·Todo 데이터를 모두 비운다.
# SonarQube 볼륨은 건드리지 않는다 (그래서 `docker compose down -v` 대신 데모 볼륨만 지정해 삭제).
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT="$(docker compose config --format json | python3 -c 'import sys,json; print(json.load(sys.stdin)["name"])')"

docker compose down
for v in prometheus_data loki_data agent_data grafana_data; do
  docker volume rm "${PROJECT}_${v}" >/dev/null 2>&1 && echo "removed volume ${PROJECT}_${v}" || true
done
docker compose up -d --build
echo "초기화 완료. 지표가 쌓이도록 1~2분 기다린 뒤 시작하세요."
