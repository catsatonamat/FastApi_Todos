# FastAPI Todos OWASP Top 10 보안 분석 보고서

## 요약

OWASP Top 10:2025의 10개 항목 중 9개에서 문제가 확인됐고, 인젝션(A05)만 발견 사항이 없습니다. 로컬 실습용으로는 동작하지만, 지금 상태로 외부 네트워크에 공개하면 누구나 데이터를 읽고 지울 수 있습니다.

가장 시급한 세 가지는 다음과 같습니다.

1. **인증과 권한 검사가 전혀 없습니다.** 모든 API가 열려 있고, docker-compose가 5001번 포트를 모든 네트워크 인터페이스에 공개합니다.
2. **동시 요청에서 데이터가 유실됩니다.** 동시에 200건을 생성하는 시험에서 200건 모두 201 응답을 받았지만, 파일에는 대부분 1건만 남았고 파일 자체가 손상된 채 남기도 했습니다.
3. **데이터 파일이 손상되면 경고 없이 전체를 비웁니다.** `todo.json` 파싱에 실패하면 로그도 백업도 없이 `[]`로 덮어씁니다.

잘 되어 있는 점도 있습니다. 화면은 사용자 입력을 `textContent`로만 출력해 XSS가 없고, 컨테이너는 비root 사용자, `cap_drop: ALL`, `no-new-privileges`로 실행됩니다.

## OWASP Top 10이란

OWASP Top 10은 웹 애플리케이션에서 가장 치명적인 보안 위험 10가지를 정리한 표준 인식 문서입니다. 비영리 재단 OWASP(Open Worldwide Application Security Project)가 실제 취약점 데이터와 업계 설문을 바탕으로 몇 년마다 개정합니다. 이 보고서는 최신판인 [OWASP Top 10:2025](https://top10.owasp.org/2025/)를 기준으로 했습니다.

| 코드 | 항목 | 의미 |
|---|---|---|
| A01 | Broken Access Control (접근 통제 실패) | 권한 없는 사용자가 데이터나 기능에 접근. 2025판에서 SSRF가 여기로 통합 |
| A02 | Security Misconfiguration (보안 설정 오류) | 불필요한 기능 노출, 기본 설정, 보안 헤더 누락 |
| A03 | Software Supply Chain Failures (소프트웨어 공급망 실패) | 의존성, 빌드, 배포 경로의 취약점과 변조. 2021판 '취약하고 오래된 구성요소'를 확장 |
| A04 | Cryptographic Failures (암호화 실패) | 전송·저장 데이터의 암호화 부재나 약한 암호 |
| A05 | Injection (인젝션) | SQL, 명령어, XSS 등 입력이 코드로 해석되는 문제 |
| A06 | Insecure Design (안전하지 않은 설계) | 구현이 아닌 설계 단계의 통제 누락 |
| A07 | Authentication Failures (인증 실패) | 사용자 확인 절차의 부재나 약점 |
| A08 | Software or Data Integrity Failures (소프트웨어·데이터 무결성 실패) | 검증 없이 코드나 데이터를 신뢰 |
| A09 | Security Logging and Alerting Failures (보안 로깅·경보 실패) | 공격과 이상 징후를 기록하거나 알리지 못함 |
| A10 | Mishandling of Exceptional Conditions (예외 상황 처리 오류) | 오류 처리 미흡, 실패 시 안전하지 않은 상태로 전환. 2025판 신설 |

2021판과 비교한 변경 내용은 [2025판 소개 문서](https://top10.owasp.org/2025/0x00_2025-Introduction/)에서 확인했습니다.

## 분석 범위와 방법

저장소 `FastApi_Todos`의 커밋 `7e63f57` 기준으로, 소스 코드를 직접 읽고 일부 동작을 격리된 환경에서 실행해 확인했습니다.

| 대상 | 파일 |
|---|---|
| 백엔드 API | `fastapi-app/main.py` (145줄, 엔드포인트 6개) |
| 프론트엔드 | `fastapi-app/templates/index.html` |
| 컨테이너·배포 | `fastapi-app/Dockerfile`, `fastapi-app/.dockerignore`, `docker-compose.yml` |
| 의존성 | `fastapi-app/requirements.txt`, 로컬 venv에 설치된 버전 |
| 기타 설정 | `fastapi-app/sonar-project.properties`, `fastapi-app/pyproject.toml`, `fastapi-app/.gitignore` |
| 테스트·보고서 | `fastapi-app/tests/`, `fastapi-app/ui_tests/`, `fastapi-app/integration/`, `fastapi-app/ui_report/REPORT.md` |

동작 확인은 FastAPI `TestClient`로 했고, 데이터 파일을 임시 디렉터리로 바꿔 실제 `todo.json`은 건드리지 않았습니다. 확인한 항목은 응답 헤더, 입력 크기 제한, Content-Type 처리, 손상된 데이터 파일 처리, 200건 동시 생성(5회 반복)입니다.

심각도는 이 앱이 네트워크에 공개된다고 가정하고 매긴 분석자의 판단이며, CVSS 점수가 아닙니다.

## 항목별 결과

높음 4개, 중간 4개, 낮음 1개, 발견 없음 1개입니다.

| 항목 | 심각도 | 핵심 발견 |
|---|---|---|
| A01 접근 통제 실패 | 높음 | 모든 API에 권한 검사 없음, 포트가 모든 인터페이스에 공개 |
| A02 보안 설정 오류 | 중간 | `/docs` 등 API 문서 공개, 보안 헤더 없음, 운영 이미지에 테스트 도구 포함 |
| A03 소프트웨어 공급망 실패 | 중간 | 의존성 버전 미고정, 잠금 파일·취약점 스캔 없음 |
| A04 암호화 실패 | 낮음 | HTTPS 없이 평문 HTTP로만 제공 |
| A05 인젝션 | 발견 없음 | XSS, SQL, 명령어, 경로 인젝션 경로 없음 |
| A06 안전하지 않은 설계 | 높음 | 파일 잠금 없는 읽기-수정-쓰기로 데이터 유실, 입력 크기·요청 횟수 제한 없음 |
| A07 인증 실패 | 높음 | 인증 수단 자체가 없음 |
| A08 무결성 실패 | 중간 | CDN 스크립트에 SRI 없음, 데이터 파일을 검증 없이 신뢰 |
| A09 로깅·경보 실패 | 중간 | 애플리케이션 로그와 감사 기록 없음 |
| A10 예외 상황 처리 오류 | 높음 | 파싱 실패 시 전체 데이터 삭제, 비정상 데이터에 500 오류 |

## 상세 발견 사항

### A01 접근 통제 실패 — 높음

네트워크로 닿을 수 있는 누구나 모든 할 일을 조회, 수정, 삭제할 수 있습니다.

- `main.py`의 엔드포인트 6개(77~145행) 어디에도 인증이나 권한 검사가 없고, 사용자 개념 없이 목록 하나를 모두가 공유합니다.
- `docker-compose.yml` 8행의 `"5001:8000"`은 호스트의 모든 인터페이스(0.0.0.0)에 포트를 엽니다. 같은 Wi-Fi의 다른 기기나, 공인 IP를 가진 서버라면 인터넷 전체에서 접근됩니다.
- 재현: `curl -X DELETE http://<호스트>:5001/todos/1` 한 줄로 삭제됩니다. id가 1부터 순차 증가하므로 전체 삭제도 쉽습니다.

다른 웹사이트를 통한 요청 위조(CSRF)는 현재 막혀 있습니다. CORS 설정이 없어 PUT·DELETE는 브라우저 사전 요청에서 거부되고(OPTIONS 405), 사전 요청 없이 보낼 수 있는 POST는 본문이 `text/plain`, 폼 형식이거나 Content-Type이 없으면 422로 거부됨을 확인했습니다.

### A02 보안 설정 오류 — 중간

- `/docs`, `/redoc`, `/openapi.json`이 모두 200으로 응답해 API 구조가 공개됩니다(`main.py` 10행의 기본 설정).
- 응답에 `Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options` 헤더가 없습니다. 화면을 다른 사이트가 iframe에 넣어 클릭을 유도할 수 있습니다.
- `requirements.txt` 하나에 운영용과 테스트용(`pytest`, `pytest-cov`, `pytest-html`)이 섞여 있고, `fastapi-app/.dockerignore`가 `tests/`, `ui_tests/`, `integration/`, `ui_report/`를 제외하지 않아 운영 이미지에 함께 들어갑니다.
- fastapi-app 컨테이너에 메모리·CPU 제한과 `read_only` 설정이 없습니다. SonarQube 컨테이너에는 `read_only`가 적용돼 있습니다.
- SonarQube도 `"9000:9000"`으로 모든 인터페이스에 공개됩니다. 초기 관리자 비밀번호를 바꾸기 전까지 특히 위험합니다.
- `fastapi-app/.gitignore`에 `.env`, `coverage.xml`, `.pytest_cache/`가 없어 나중에 비밀 값이 실수로 커밋될 여지가 있습니다.

### A03 소프트웨어 공급망 실패 — 중간

- `requirements.txt`의 8개 중 7개가 `>=`로만 지정돼 빌드할 때마다 다른 버전이 설치될 수 있습니다. 잠금 파일과 해시 검증이 없습니다.
- `Dockerfile` 2행의 `python:3.13-slim`은 다이제스트로 고정되지 않았습니다.
- 저장소에 의존성 취약점 스캔(`pip-audit`, Dependabot 등) 설정이 없습니다. SonarQube 커뮤니티판은 코드 품질 위주라 이를 대신하지 못합니다.
- `jinja2`는 선언돼 있지만 코드에서 쓰이지 않습니다. 화면은 `FileResponse`로 제공됩니다.
- 반대로 UI 테스트가 쓰는 `playwright`, `pytest-playwright`는 로컬 venv에만 설치돼 있고 `requirements.txt`에 선언되지 않았습니다. 어떤 버전으로 테스트했는지 저장소만으로는 알 수 없습니다.

긍정적인 점으로, 로컬에 설치된 fastapi 0.142.2와 starlette 1.7.0은 `requirements.txt` 주석이 언급한 보안 패치 버전 이상입니다. 다만 로컬 venv는 Python 3.12이고 컨테이너는 3.13이며 버전도 고정돼 있지 않아, 컨테이너에 같은 버전이 설치된다는 보장은 없습니다.

### A04 암호화 실패 — 낮음

`Dockerfile` 30행의 uvicorn은 TLS 없이 평문 HTTP로만 서비스합니다. 비밀번호나 토큰 같은 민감 정보를 다루지 않아 영향은 작지만, 같은 네트워크에서 내용을 엿보거나 바꿀 수 있습니다. 인증을 추가하면 HTTPS가 필수가 됩니다.

### A05 인젝션 — 발견 없음

- **XSS**: `index.html`은 제목, 설명, 마감일을 모두 `textContent`와 `createElement`로 출력합니다(382, 388, 396행). `innerHTML`은 고정 문자열에만 쓰입니다(315, 317, 416행).
- **SQL·명령어**: 데이터베이스와 셸 호출이 없습니다.
- **경로**: `todo_id`는 `int`, `direction`은 `Literal["up", "down"]`으로 검증되고, 파일 경로는 사용자 입력과 무관한 고정 값입니다.

### A06 안전하지 않은 설계 — 높음

- **동시 쓰기로 데이터가 유실됩니다.** 모든 변경이 파일 전체를 읽고, 고치고, 다시 쓰는데 잠금이 없습니다(`load_todos` 33행, `save_todos` 56행). 동시 생성 200건 시험을 5회 반복했을 때 매번 200건 모두 201을 받았지만, 4회는 파일에 1건만 남았고 1회는 파일이 파싱할 수 없는 상태로 남았습니다. id도 중복 발급됩니다. 한 차례 시험에서 200건의 응답에 서로 다른 id가 6개뿐이었고, 133건이 id 1을 받았습니다. `move_todo`의 주석(118행)은 경쟁 상태를 방지한다고 하지만, 요청 간 경쟁은 막지 못합니다.
- **입력 크기 제한이 없습니다.** 2MB짜리 제목이 201로 저장됐습니다. 항목 수 제한과 요청 횟수 제한도 없어, 반복 요청으로 디스크와 메모리를 소진시킬 수 있습니다.
- **서버 검증이 화면 검증보다 약합니다.** 화면은 빈 제목을 막지만 API는 `"title": ""`와 공백만 있는 제목을 201로 받습니다. PUT으로는 `order`에 음수나 중복 값도 넣을 수 있습니다.
- `todo.json`이 볼륨 없이 컨테이너 안에만 있어, 컨테이너를 다시 만들면 데이터가 사라집니다.

### A07 인증 실패 — 높음

로그인, API 키, 세션 등 사용자를 확인하는 수단이 없습니다. A01과 원인이 같으며, 인증이 없으니 누가 무엇을 바꿨는지도 구분할 수 없습니다. 약한 비밀번호나 세션 관리 같은 세부 항목은 인증이 생긴 뒤에 점검 대상이 됩니다.

### A08 소프트웨어·데이터 무결성 실패 — 중간

- `index.html` 234행이 jsDelivr CDN에서 `canvas-confetti@1.9.3`을 `integrity` 속성 없이 불러옵니다. CDN이나 패키지가 변조되면 그 스크립트가 이 페이지 권한으로 실행돼 모든 API를 호출할 수 있습니다.
- `load_todos`는 파일 내용을 스키마 검증 없이 그대로 믿습니다. 배열이 아닌 JSON이나 id가 없는 항목이 있으면 대부분의 엔드포인트가 500 오류를 냅니다(상세는 A10).
- 개발 중 데이터가 담긴 `todo.json`이 저장소에 커밋돼 있고 이미지에도 복사됩니다.

### A09 보안 로깅·경보 실패 — 중간

- `main.py`에 로깅 코드가 없습니다. 남는 기록은 uvicorn 기본 접근 로그뿐이라, 어떤 항목이 어떻게 바뀌거나 지워졌는지 추적할 수 없습니다.
- 데이터 파일을 초기화하는 중대한 사건(40~43행)도 기록되지 않습니다.
- 대량 삭제나 반복 오류를 감지해 알리는 장치가 없습니다.

### A10 예외 상황 처리 오류 — 높음

- **파싱 실패 시 전체 데이터를 지웁니다.** `load_todos` 40~43행은 `JSONDecodeError`가 나면 파일을 `[]`로 덮어씁니다. 손상된 파일이나 빈 파일을 넣고 목록을 조회하자 파일이 `[]`로 바뀌는 것을 확인했습니다.
- **이 동작이 A06의 경쟁 상태와 겹칩니다.** 동시 생성 시험 5회 중 1회에서 여러 요청의 쓰기가 섞여 `todo.json`이 파싱할 수 없는 상태로 남았습니다. 이 상태에서 다음 요청이 들어오면 위 동작에 따라 전체가 지워집니다. 즉 외부에서 동시 요청만 보내도 데이터 전체 삭제로 이어질 수 있습니다. 또한 `write_text`는 파일을 비운 뒤 쓰므로, 쓰는 도중 다른 요청이 읽거나 디스크가 가득 차 쓰기가 중단돼도 같은 결과가 됩니다. 이 두 경우는 코드에서 추론한 것이며 따로 재현하지는 않았습니다.
- **처리되지 않는 예외가 있습니다.** 파일에 id 없는 항목이 있거나 내용이 배열이 아니면(`{"a": 1}`, `5`, `null` 등) 조회, 생성, 수정, 이동, 삭제가 모두 500을 반환합니다(64, 108, 121, 141행의 `t["id"]` 등). 빈 객체 `{}`만 예외로, 조회는 200이고 생성만 500입니다. 파일 쓰기 실패(`OSError`)도 처리하지 않습니다. 500 응답 본문은 `Internal Server Error`뿐이라 내부 정보가 노출되지는 않습니다.
- **외부 스크립트 실패가 핵심 기능을 막습니다.** CDN이 차단되면 `index.html` 367행의 `fireConfetti()`가 오류를 내고, 바로 다음 줄의 완료 상태 저장이 실행되지 않습니다. `ui_report/REPORT.md`의 유일한 실패 테스트가 이 경우입니다.

## 권고 조치

1순위 네 가지만 처리해도 높음 등급 항목이 모두 완화됩니다.

| 우선순위 | 조치 | 해결 항목 |
|---|---|---|
| 1 | 포트를 `"127.0.0.1:5001:8000"`, `"127.0.0.1:9000:9000"`으로 바꿔 로컬에만 공개 | A01, A02 |
| 1 | 외부 공개가 필요하면 인증 추가(최소한 API 키 헤더, 다중 사용자면 로그인과 항목별 소유자 검사) | A01, A07 |
| 1 | 읽기-수정-쓰기 전체를 `threading.Lock`으로 감싸고, 임시 파일에 쓴 뒤 `os.replace`로 교체 | A06, A10 |
| 1 | 파싱 실패 시 덮어쓰지 말고 원본을 `.bak`으로 보존, 로그를 남기고 503 반환 | A10, A09 |
| 2 | `title`, `description`에 길이 제한, `order`에 범위 제한, 항목 수 상한 추가 | A06 |
| 2 | CDN 스크립트에 `integrity`와 `crossorigin` 추가 또는 파일을 직접 호스팅, `fireConfetti()`를 try/catch로 감싸기 | A08, A10 |
| 2 | 생성·수정·삭제와 오류를 `logging`으로 기록 | A09 |
| 2 | 의존성을 정확한 버전으로 고정하고 운영용과 개발용 파일 분리(개발용에 `playwright`, `pytest-playwright` 포함), CI에 `pip-audit` 추가 | A03, A02 |
| 3 | 보안 헤더 미들웨어 추가, 운영에서 `docs_url=None`, `redoc_url=None`, `openapi_url=None` | A02 |
| 3 | `.dockerignore`에 테스트·보고서 디렉터리 추가, 컨테이너에 `read_only`와 메모리 제한, 데이터 디렉터리를 볼륨으로 분리 | A02, A06 |
| 3 | 외부 공개 시 리버스 프록시로 HTTPS 적용 | A04 |

### 입력 제한

입력 제한은 모델에서 바로 적용할 수 있습니다. 이때 입력 모델과 출력 모델을 분리해야 합니다. 지금은 `TodoItem`이 `TodoIn`을 상속하고 응답 검증에도 쓰이므로, 제약을 `TodoIn`에만 넣으면 파일에 이미 있는 빈 제목이나 음수 `order` 항목 때문에 `GET /todos`가 500이 됩니다.

```python
from typing import Annotated
from pydantic import BaseModel, Field, StringConstraints

class TodoIn(BaseModel):
    """입력용: 제약을 건다. 앞뒤 공백을 제거해 공백만 있는 제목도 거부한다."""
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    description: str = Field(default="", max_length=2000)
    done: bool = False
    order: int = Field(default=0, ge=0, le=100_000)
    due_date: date | None = None


class TodoItem(BaseModel):
    """출력용: TodoIn을 상속하지 않아, 제약 도입 전에 저장된 항목도 조회된다."""
    id: int
    title: str
    description: str = ""
    done: bool = False
    order: int = 0
    due_date: date | None = None
```

### 잠금과 원자적 쓰기

잠금과 원자적 쓰기는 다음 형태가 됩니다. 각 엔드포인트는 읽기부터 저장까지를 `with _lock:` 안에서 수행해야 합니다.

```python
import os, threading

_lock = threading.Lock()

def save_todos(todos: list[dict]) -> None:
    tmp = DATA_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(todos, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, DATA_FILE)  # 읽는 쪽은 항상 완전한 파일만 봄
```

적용할 때 주의할 점입니다.

- 이 잠금은 uvicorn 워커가 1개일 때만 유효합니다. 워커를 늘리거나 사용자가 많아지면 SQLite 같은 데이터베이스로 옮기는 편이 근본적인 해결입니다.
- `os.replace`는 `todo.json`이 있는 디렉터리에 쓰기 권한이 있어야 동작합니다. 볼륨은 `todo.json` 파일 하나가 아니라 디렉터리 단위로 마운트해야 하며(예: `DATA_FILE`을 `/app/data/todo.json`으로 옮기고 `/app/data`를 볼륨으로), 파일 하나만 바인드 마운트하면 교체가 실패합니다. 컨테이너에 `read_only`를 적용해도 이 디렉터리는 쓰기 가능해야 합니다.

## 한계와 후속 작업

이 보고서는 저장소 코드와 로컬 실행만으로 작성했으며, 다음은 확인하지 못했습니다.

- **의존성의 알려진 취약점(CVE).** 가상환경에 `pip-audit`가 없어 자동 스캔을 하지 않았습니다. 버전 목록만 확인했고, `requirements.txt` 주석에 적힌 CVE가 해당 버전에서 실제로 패치됐는지도 따로 대조하지 않았습니다.
- **실제 배포 환경.** 주석에 Jenkins가 언급되지만 파이프라인 정의가 저장소에 없습니다. 서버의 방화벽, 리버스 프록시, Jenkins 자격 증명 관리는 점검 범위 밖입니다.
- **실행 중인 컨테이너.** 동시성 시험은 `TestClient`와 스레드로 했고, Docker로 띄운 uvicorn에 부하를 주지는 않았습니다. 로컬 실행 환경(Python 3.12)도 컨테이너(Python 3.13)와 다릅니다.
- **컨테이너 이미지 취약점.** 베이스 이미지와 SonarQube 이미지는 스캔하지 않았습니다.

후속으로 권하는 작업입니다.

- [ ] `pip-audit -r requirements.txt` 실행
- [ ] Trivy 등으로 컨테이너 이미지 스캔
- [ ] OWASP ZAP으로 실행 중인 앱 동적 점검
- [ ] 1순위 조치 적용 후 동시 생성 시험을 회귀 테스트로 추가
