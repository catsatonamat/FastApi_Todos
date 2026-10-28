FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# root가 아닌 실행 전용 사용자. 감사 로그를 쓰는 /data만 appuser 소유로 둔다
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /data && chown appuser:appuser /data

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY ops_agent/ ./ops_agent/

USER appuser
EXPOSE 8080
CMD ["python", "-m", "ops_agent"]
