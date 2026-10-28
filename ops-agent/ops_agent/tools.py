"""LLM이 호출할 수 있는 조사 도구 (모두 읽기 전용).

변경 조치(재시작 등)는 여기에 없다 — 사람 승인을 거쳐 actions.py에서만 실행된다.
"""
import json

import aiohttp

from .observability import Observability

MAX_RESULT_CHARS = 3500

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "query_prometheus",
            "description": "PromQL instant query로 현재 값을 조회한다.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "PromQL 식"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_prometheus_range",
            "description": "PromQL을 최근 N분 구간으로 조회해 시간에 따른 변화(언제부터 나빠졌는지)를 본다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "PromQL 식"},
                    "minutes": {"type": "integer", "description": "조회 구간(분), 기본 10"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_logs",
            "description": "LogQL로 Loki에서 최근 로그를 검색한다. 최신순으로 최대 30줄.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": 'LogQL 식 (예: {service="fastapi-app", level="ERROR"})'},
                    "minutes": {"type": "integer", "description": "조회 구간(분), 기본 5"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_app_health",
            "description": "앱의 /health 엔드포인트를 직접 호출해 상태 코드와 응답 시간을 확인한다.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_runtime_config",
            "description": "앱의 런타임 설정(피처 플래그·장애 주입 설정)과 마지막 변경 시각을 조회한다. 최근 설정 변경이 장애와 관련 있는지 확인할 때 쓴다.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


async def run_tool(obs: Observability, name: str, args: dict, log_window: int | None = None) -> str:
    """도구를 실행하고 결과를 JSON 문자열로 돌려준다. 실패도 문자열로 돌려 LLM이 다음 행동을 고르게 한다.

    log_window(분)를 주면 로그 검색 구간을 그 안으로 제한한다 (이전 장애의 로그가 섞이지 않도록).
    """
    try:
        if name == "query_prometheus":
            result = await obs.promql(args["query"])
        elif name == "query_prometheus_range":
            result = await obs.promql_range(args["query"], int(args.get("minutes") or 10))
        elif name == "search_logs":
            minutes = int(args.get("minutes") or 5)
            result = await obs.logs(args["query"], min(minutes, log_window) if log_window else minutes)
        elif name == "get_app_health":
            result = await obs.app_health()
        elif name == "get_runtime_config":
            result = await obs.runtime_config()
        else:
            result = {"error": f"알 수 없는 도구: {name}"}
    except KeyError as e:
        result = {"error": f"필수 인자 누락: {e}"}
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        result = {"error": f"{type(e).__name__}: {e}"}

    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "...(truncated)"
    return text
