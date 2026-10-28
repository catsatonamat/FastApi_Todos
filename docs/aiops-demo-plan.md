# Agentic AIOps Hands-on Demo 개발 계획

> 주제: Operate/Monitor 단계에 Agentic DevOps·AIOps를 적용한 FastAPI(Todo) 운영 자동화
> 시나리오: Todo API 저장소 장애 → 알림 → AI 에이전트 조사(RCA) → Discord 보고 → 사람 승인 → 조치 → 복구 검증

## 1. 확정된 범위

| 항목 | 결정 |
|---|---|
| 대상 앱 | 기존 `fastapi-app`(Todo API) 확장 |
| LLM | Ollama(`qwen3`, 로컬) 기본, `LLM_PROVIDER=gemini`로 Gemini API 전환 |
| 운영 채널 | Discord 봇 (Embed 보고 + 버튼 승인 + 슬래시 명령) |
| 범위 | 알림 → RCA → 보고 → **승인형 조치** → 복구 검증 (폐루프) |

## 2. 아키텍처

```text
 loadgen ──HTTP──▶ todo-app (FastAPI)
                     │  /metrics (Prometheus 포맷)
                     │  stdout JSON 로그
                     ▼
   Prometheus ◀─scrape─┘        Alloy ──(docker 로그 수집)──▶ Loki
       │ alert_rules                                           │
       ▼                                                      │
   Alertmanager ──webhook──▶ ops-agent ◀──LogQL──────────────┘
                              │   ├─ PromQL 조회 (Prometheus API)
                              │   ├─ LLM (Ollama / Gemini, tool-calling)
                              │   └─ 조치 실행 (allowlist)
                              │        ├─ 장애 플래그 해제 (앱 admin API)
                              │        ├─ 컨테이너 재시작 (docker-proxy-ops: restart/stop/kill만 허용)
                              │        └─ GitHub 이슈 생성 (토큰 없으면 초안만)
                              ▼
                         Discord 채널 (보고 Embed + 승인 버튼 + 스레드 진행 로그)
   Grafana: Prometheus + Loki 대시보드 (시연 화면용)
```

### 에이전트 도구 사용 루프
1. Alertmanager 웹훅 수신 → 인시던트 생성(groupKey 기준 중복 제거)
2. Discord에 "🚨 조사 중" 메시지 + 스레드 생성
3. **기본 증거 수집(결정적)**: 에러율, p95(route별), 트래픽, `up`, 최근 ERROR/WARNING 로그, 현재 런타임 설정 → LLM 실패 시에도 보고 가능
4. **LLM 조사 루프(최대 N 스텝)**: `query_prometheus`, `search_logs`, `get_app_health` 도구를 LLM이 선택 호출 → 각 호출을 스레드에 실시간 표시
5. **구조화 보고서 생성(JSON 스키마)**: 요약 / 원인 후보(신뢰도·근거) / 영향 / 위험도 / 권장 조치(allowlist ID)
6. Discord Embed + 버튼(장애 플래그 해제 · 재시작 · 이슈 생성 · 무시)
7. 승인자 권한 확인 → 조치 실행 → 감사 로그(`audit.jsonl`) 기록
8. 30초 후 메트릭 재조회로 **복구 검증** → Alertmanager resolved 수신 시 MTTR 표시

## 3. 장애 시나리오 (Todo 앱)

| 시나리오 | 주입 방법 | 기대 알림 | 정답 조치 |
|---|---|---|---|
| `storage_error` | 저장소 읽기/쓰기 시 `StorageUnavailableError` (확률 p) → 500 | `TodoHighErrorRate` | 장애 플래그 해제 또는 재시작 |
| `slow_storage` | 저장소 접근마다 지연 (ms) | `TodoHighLatency` | 장애 플래그 해제 또는 재시작 |
| `down` | `docker stop` | `TodoAppDown` | 재시작 |

장애 상태는 메모리에 있으므로 **재시작하면 사라짐** → "재시작 승인 → 복구"가 자연스럽게 시연됨.
주입은 `PUT /admin/chaos` (헤더 `X-Admin-Token`), `scripts/chaos.sh`, 또는 Discord `/chaos` 명령으로.

## 4. 디렉터리 구조

```text
FastApi_Todos/
├─ fastapi-app/            # 기존 앱 + observability.py, chaos.py
├─ ops-agent/              # 웹훅 서버 + 에이전트 + Discord 봇 (단일 프로세스)
│  └─ ops_agent/
│     ├─ config.py          환경변수 설정 (LLM_PROVIDER=ollama|gemini)
│     ├─ llm.py             Ollama 네이티브 / Gemini OpenAI 호환 클라이언트
│     ├─ observability.py   Prometheus·Loki·앱 조회 (읽기 전용)
│     ├─ tools.py           LLM 도구 정의·실행 (읽기 전용 5종)
│     ├─ agent.py           기본 증거 → 도구 루프 → JSON 스키마 보고서
│     ├─ actions.py         allowlist 조치 3종 + 감사 로그
│     ├─ service.py         인시던트 수명주기 (dedup, 조치, 복구 검증, resolved)
│     ├─ discord_bot.py     Embed·스레드·버튼·/status /chaos /incidents
│     └─ webhook.py         Alertmanager 웹훅 + 콘솔 모드 API
├─ loadgen/                # 트래픽 생성기
├─ monitoring/
│  ├─ prometheus/  prometheus.yml, alert_rules.yml
│  ├─ alertmanager/alertmanager.yml
│  ├─ loki/loki.yml
│  ├─ alloy/config.alloy
│  └─ grafana/provisioning/ (datasources, dashboards)
├─ scripts/chaos.sh
├─ docker-compose.yml      # sonarqube는 profile "sonar"로 분리
└─ .env.example
```

## 5. 단계별 작업

| Phase | 내용 | 완료 기준 |
|---|---|---|
| 1. 앱 관측성 | Prometheus 메트릭 미들웨어, JSON 구조화 로그, `/health`, 장애 주입 API, 테스트 | 기존+신규 pytest 통과, `/metrics` 노출 |
| 2. 관측 스택 | Prometheus, Alertmanager, Loki, Alloy, Grafana 프로비저닝, loadgen | Grafana에서 지표·로그 확인, 장애 주입 시 알림 firing |
| 3. 에이전트 | LLM 추상화(OpenAI 호환 API로 Ollama/Gemini 공통), 도구, 조사 루프, 보고서 스키마 | 웹훅 → 보고서 JSON 생성 (Discord 없이 콘솔 모드로 검증) |
| 4. Discord 봇 | Embed 보고, 스레드 진행 로그, 승인 버튼, `/status` `/chaos` 명령, 승인자 제한 | 실제 서버에서 버튼 승인 → 조치 → 복구 검증 |
| 5. 조치·가드레일 | allowlist 조치, socket-proxy, 감사 로그, 복구 검증, resolved/MTTR | 감사 로그 기록, 재시작 후 알림 resolved |
| 6. 시연 리허설 | `docs/demo-runbook.md` (녹화 순서·명령어), 타이밍 튜닝 | 장애 주입부터 보고까지 2분 이내 |

## 5-1. 진행 상황 (2026-10-26)
- [x] Phase 1~3, 5: 구현·단위 테스트 완료, 콘솔 모드로 3개 시나리오 E2E 확인
- [x] Phase 4: Discord 봇 구현·렌더링 테스트 완료 — **실제 서버 연결은 봇 토큰 발급 후 확인 필요**
- [ ] Phase 6: 리허설·녹화 ([demo-runbook.md](demo-runbook.md))

### 개발 중 발견한 것 (발표 '한계' 파트 소재)
- Ollama 기본 컨텍스트(4K)에서는 증거가 잘려 RCA 품질이 크게 떨어짐 → `num_ctx=16384`
- 8B 로컬 모델은 잘못된 PromQL, 같은 도구 반복 호출, 이전 장애 로그를 근거로 드는 문제가 있음
  → 결정적 기본 증거 수집, 동일 호출 차단, 로그 검색 구간을 인시던트 시간으로 제한해서 보완
- "변경 이력"을 증거에 넣으면 원인 추정 정확도가 크게 오름 (상용 AIOps의 change correlation과 같은 원리)

## 6. 가드레일 (발표 포인트)
- **도구 권한 최소화**: LLM이 호출할 수 있는 도구는 읽기 전용(조회)뿐. 변경 조치는 버튼 승인으로만 실행.
- **Allowlist**: 조치는 사전 정의된 3개만. LLM은 ID만 "추천"하고 실행 권한 없음.
- **Docker 권한 축소**: docker.sock 직접 마운트 대신 socket-proxy 사용. 로그 수집용(읽기 전용)과 조치용(restart/stop/kill만)을 분리.
- **승인자 제한**: `DISCORD_APPROVER_ROLE` / `DISCORD_APPROVER_IDS`.
- **감사 로그**: 누가·언제·무엇을 승인했고 결과가 어땠는지 `audit.jsonl`에 기록.
- **LLM 실패 대비**: 결정적 기본 증거 + 보고서 파싱 실패 시 원본 증거만으로 보고.

## 7. 사전 준비물 (사용자)
1. Discord 개발자 포털에서 봇 생성 → `DISCORD_TOKEN`, 서버 초대(권한: Send Messages, Create Public Threads, Embed Links, `applications.commands`)
2. 보고 채널 ID → `DISCORD_CHANNEL_ID`
3. (선택) `GEMINI_API_KEY`, `GITHUB_TOKEN` + `GITHUB_REPO`
4. Ollama 실행 + `ollama pull qwen3`
