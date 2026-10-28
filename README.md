# todo-list
a simple todo list app implemented with FastAPI.

## 실행 방법 (macOS)

### 1. 프로젝트 폴더로 이동
```bash
cd FastApi_Todos/fastapi-app
```

### 2. 가상환경 생성 및 활성화
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. 의존성 설치
```bash
pip install -r requirements.txt
```

### 4. 서버 실행
```bash
uvicorn main:app --reload
```

### 5. 브라우저에서 접속
```
http://localhost:8000
```

서버를 종료하려면 터미널에서 `Ctrl + C`를 누르세요.
가상환경을 벗어나려면 `deactivate`를 입력하세요.

## AIOps 데모: AI 운영 에이전트 + Discord

Todo API에 장애를 주입하면 AI 에이전트가 메트릭·로그를 조사해 원인을 분석하고, Discord로 보고한 뒤 사람이 승인한 조치를 실행합니다.

```text
loadgen → Todo API ─/metrics→ Prometheus → Alertmanager ─webhook→ ops-agent ─→ Discord (보고·승인 버튼)
                    └stdout JSON 로그→ Alloy → Loki ──────────────────↗  └─ 조치: 장애 플래그 해제 / 재시작 / 이슈
```

```bash
cp .env.example .env          # Discord 토큰 등 입력 (비우면 콘솔 모드)
docker compose up -d --build  # SonarQube는 필요할 때: docker compose --profile sonar up -d sonarqube
./scripts/chaos.sh error 0.5  # 장애 주입 → 약 2분 안에 Discord 보고
```

- 설계·개발 계획: [docs/aiops-demo-plan.md](docs/aiops-demo-plan.md)
- 실행·녹화 런북: [docs/demo-runbook.md](docs/demo-runbook.md)
