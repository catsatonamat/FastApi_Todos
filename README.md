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
