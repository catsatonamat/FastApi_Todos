# AIOps Hands-on Demo 런북

FastAPI Todo API 장애를 AI 운영 에이전트가 **탐지 → 조사(RCA) → Discord 보고 → 사람 승인 → 조치 → 복구 검증**까지 처리하는 데모.
설계와 단계별 계획은 [aiops-demo-plan.md](aiops-demo-plan.md) 참고.
장면별 화면·입력·멘트는 [demo-script.md](demo-script.md) 참고.

## 1. 사전 준비

### 1-1. Ollama (호스트에서 실행)
```bash
ollama pull qwen3:8b
ollama serve            # 이미 앱으로 떠 있으면 생략
```
Gemini로 바꾸려면 `.env`에서 `LLM_PROVIDER=gemini`, `GEMINI_API_KEY=...` 설정 후 `docker compose up -d ops-agent`.

### 1-2. Discord 봇 만들기
1. <https://discord.com/developers/applications> → **New Application**
2. **Bot** 탭 → **Reset Token** → 토큰을 `.env`의 `DISCORD_TOKEN`에 넣는다 (Privileged Intents는 필요 없음)
3. **OAuth2 → URL Generator**
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: View Channels, Send Messages, Send Messages in Threads, Create Public Threads, Embed Links, Read Message History
   - 생성된 URL로 데모용 서버에 초대
4. Discord 설정 → 고급 → **개발자 모드** 켜기
   - 보고 채널 우클릭 → ID 복사 → `DISCORD_CHANNEL_ID`
   - 서버 아이콘 우클릭 → ID 복사 → `DISCORD_GUILD_ID` (슬래시 명령이 즉시 등록됨)
5. (선택) 승인자 제한: `DISCORD_APPROVER_ROLE=oncall` 또는 `DISCORD_APPROVER_IDS=내ID`
   - 역할을 지정하면 보고서가 올라올 때 그 역할을 멘션한다

### 1-3. 실행
```bash
cp .env.example .env        # 토큰 값 채우기 (CHAOS_ADMIN_TOKEN, AGENT_API_TOKEN은 임의 문자열)
docker compose up -d --build
docker compose logs -f ops-agent   # "Discord 로그인: ..." 확인
```

| 화면 | 주소 |
|---|---|
| Todo 앱 | <http://localhost:5001> |
| Grafana 대시보드 | <http://localhost:3000> (로그인 없이 조회, 관리자 admin/admin) |
| Prometheus 알림 | <http://localhost:9090/alerts> |
| Alertmanager | <http://localhost:9093> |
| 에이전트 인시던트 API | <http://localhost:8081/incidents> |

## 2. 시나리오

| 명령 | 장애 | 알림 | 기대 RCA | 정답 조치 |
|---|---|---|---|---|
| `./scripts/chaos.sh error 0.5` 또는 `/chaos error percent:50` | 저장소 I/O 오류 50% → 503 | `TodoHighErrorRate` | 런타임 설정(장애 주입) 변경 → StorageUnavailableError | 🧯 장애 플래그 해제 |
| `./scripts/chaos.sh slow 800` 또는 `/chaos slow ms:800` | 저장소 접근 800ms 지연 | `TodoHighLatency` | 설정 변경 → 저장소 지연 | 🧯 장애 플래그 해제 / 🔄 재시작 |
| `./scripts/chaos.sh down` | 앱 컨테이너 중지 | `TodoAppDown` | 앱 프로세스 응답 없음 | 🔄 재시작 |

해제: `./scripts/chaos.sh off` (또는 `/chaos off`)

### 타이밍 (qwen3:8b, M 시리즈 Mac 기준 실측)
- 장애 주입 → 알림 firing: 약 45초 (`for: 30s` + 1분 rate 창)
- 알림 → Discord 보고서: 약 100~120초 (도구 호출 3~4회 + 보고서 생성, Gemini는 더 빠름)
- 조치 승인 → 복구 검증 메시지: 30초 (`VERIFY_DELAY`)
- 복구 → Alertmanager resolved: 약 1분

녹화할 때는 대기 구간을 컷 편집하거나 배속으로 처리한다.

> 시나리오를 연달아 돌릴 때는 이전 알림이 resolved 된 뒤 **3~5분** 쉬고 주입한다. 바로 이어서 하면 이전 장애 로그가 증거 구간에 섞여 보고서에 엉뚱한 근거가 들어갈 수 있다.

## 3. 녹화 순서 (영상 4:00~9:00 구간)

1. **정상 상태** — Grafana 대시보드: 상태 UP, 에러율 0%, p95 수 ms. Discord에서 `/status`
2. **장애 주입** — Discord에서 `/chaos error percent:50` (또는 터미널 `./scripts/chaos.sh error 0.5`)
   - Todo 앱 화면을 새로고침하면 간헐적으로 실패하는 것을 보여준다
3. **탐지** — Grafana 에러율 패널이 빨갛게 변함 → Prometheus `/alerts`에서 `TodoHighErrorRate` firing
4. **에이전트 조사** — Discord 채널에 `🚨 TodoHighErrorRate 알림 수신` 메시지 + 스레드
   - 스레드에 `🔧 query_prometheus_range`, `🔧 search_logs`, `🔧 get_runtime_config` … 가 실시간으로 올라온다
   - **포인트**: 사람이 하던 "지표 확인 → 로그 검색 → 최근 변경 확인"을 에이전트가 도구 호출로 수행
5. **보고서** — 상단 메시지가 Embed로 바뀐다: 요약 / 알림 시점 지표 / 원인 후보(신뢰도·근거) / 권장 조치 / 추가 확인
   - 권장 조치 버튼은 초록색으로 강조된다
6. **사람 승인** — `🧯 장애 플래그 해제` 클릭 → 스레드에 "승인자: OOO ✅ 장애 주입 플래그를 off로 변경"
   - **포인트**: LLM은 조치를 *추천*만 하고 실행 권한이 없다. 실행은 allowlist 조치 + 사람 승인으로만
7. **복구 검증** — 30초 뒤 `✅ 복구 확인 (장애 플래그 해제 후): 에러율 0%, p95 5ms, up=1`
8. **종료** — 약 1분 뒤 `✅ RESOLVED — MTTR NNN초`, Embed가 초록색으로 바뀌고 버튼 비활성화
9. (선택) `📝 이슈 생성` → GitHub 이슈 또는 초안, `/incidents`로 이력 확인
10. **감사 로그** — `docker compose exec ops-agent cat /data/audit.jsonl`

## 3-1. 운영 지표 (영상 9:00~ 기대 효과)

| 지표 | 정의 | 확인 위치 |
|---|---|---|
| MTTD | 장애 주입 시각 → 에이전트 알림 수신 | 보고서 Embed `⏱ 운영 지표`, `/stats`, Grafana KPI 행 |
| 조사 시간 | 알림 수신 → 보고서 완성 | 위와 같음 |
| MTTR | 장애 주입 시각 → Alertmanager resolved | 위와 같음 (복구 후) |
| RCA 적중률 | 첫 권장 조치(이슈 생성 제외)가 정답인 비율. 정답: storage_error·slow_storage → 장애 플래그 해제, down → 재시작 | `/stats`, Grafana, 복구 후 Embed `RCA 판정` |

- LLM별로 따로 집계되므로 Ollama로 몇 번, Gemini로 몇 번 돌리면 **모델 비교표**가 바로 나온다.
- `down` 시나리오는 중지 시각을 알 수 없어 MTTD가 비고, MTTR은 알림 발생 시각부터 잰다.
- 기록은 `/data/incidents.jsonl`(에이전트 볼륨)에 남아 재시작해도 유지되고, `reset-demo.sh`로 초기화된다.
- JSON으로 보기: `curl -s localhost:8081/stats | python3 -m json.tool`

## 4. 가드레일 보여주기 (영상 9:00~ 한계·안전장치)
- 권한 없는 사용자가 버튼 클릭 → `⛔ 조치를 승인할 권한이 없습니다` (승인자 제한을 켠 경우)
- allowlist 밖 조치 요청은 거부된다:
  ```bash
  curl -X POST localhost:8081/incidents/<id>/actions/drop_database -H "X-Agent-Token: $AGENT_API_TOKEN"
  # → {"ok": false, "detail": "허용되지 않은 조치: drop_database"}
  ```
- 에이전트용 Docker 프록시는 restart/stop/kill만 허용한다 (create·delete·exec는 403)
- LLM이 죽어도(Ollama 중지) 기본 증거만으로 보고서가 나간다: footer에 `⚠️ LLM 실패: 기본 증거만`

## 5. Discord 없이 리허설 (콘솔 모드)
`DISCORD_TOKEN`을 비우면 보고서는 `docker compose logs ops-agent`에 찍히고, 조치는 HTTP로 승인한다.
```bash
./scripts/chaos.sh error 0.5
curl -s localhost:8081/incidents | python3 -m json.tool          # 보고서 확인
curl -X POST localhost:8081/incidents/<id>/actions/disable_fault \
     -H "X-Agent-Token: $(grep ^AGENT_API_TOKEN .env | cut -d= -f2)" -H "X-Actor: me"
```

## 6. 문제 해결
| 증상 | 확인 |
|---|---|
| 보고서 footer에 `LLM 실패` | `curl localhost:11434/api/tags` (Ollama 실행 여부), `OLLAMA_BASE_URL` |
| 슬래시 명령이 안 보임 | `DISCORD_GUILD_ID` 설정 후 `docker compose restart ops-agent` |
| 알림이 안 옴 | <http://localhost:9090/alerts>, `docker compose logs alertmanager` |
| 에이전트를 재시작했더니 기존 버튼이 "인시던트를 찾을 수 없음" | 인시던트는 메모리에 저장됨. 진행 중 알림은 최대 5분 안에 다시 수신됨 |
| Grafana 로그 패널이 비어 있음 | `docker compose logs alloy`, <http://localhost:12345> |
