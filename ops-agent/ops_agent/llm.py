"""LLM 클라이언트.

에이전트는 OpenAI Chat Completions 형식의 메시지만 다룬다.
- Gemini: OpenAI 호환 엔드포인트(v1beta/openai)를 그대로 호출
- Ollama: 네이티브 /api/chat 호출 후 OpenAI 형식으로 변환 (num_ctx·think 옵션을 쓰기 위해)
"""
import json
import re

import aiohttp

from .config import Settings


class LLMError(Exception):
    pass


class LLMClient:
    def __init__(self, settings: Settings, http: aiohttp.ClientSession):
        self.base_url = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model
        self.api_key = settings.llm_api_key
        self.reasoning_effort = settings.llm_reasoning_effort
        self.timeout = aiohttp.ClientTimeout(total=settings.llm_timeout)
        self.http = http

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_schema: dict | None = None,
    ) -> dict:
        """assistant 메시지(dict)를 반환한다. tool_calls가 있으면 그대로 포함된다."""
        body: dict = {"model": self.model, "messages": messages, "temperature": 0.2}
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        if tools:
            body["tools"] = tools
        if json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "report", "schema": json_schema},
            }
        try:
            async with self.http.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            ) as res:
                payload = await res.json(content_type=None)
                if res.status != 200:
                    raise LLMError(f"HTTP {res.status}: {json.dumps(payload, ensure_ascii=False)[:300]}")
        except (aiohttp.ClientError, TimeoutError) as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e

        message = payload["choices"][0]["message"]
        # thinking 모델이 본문에 <think> 블록을 남기는 경우 제거
        if isinstance(message.get("content"), str):
            message["content"] = re.sub(r"<think>.*?</think>", "", message["content"], flags=re.S).strip()
        return message


class OllamaClient(LLMClient):
    def __init__(self, settings: Settings, http: aiohttp.ClientSession):
        super().__init__(settings, http)
        self.num_ctx = settings.ollama_num_ctx

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_schema: dict | None = None,
    ) -> dict:
        body: dict = {
            "model": self.model,
            "messages": to_ollama_messages(messages),
            "stream": False,
            "options": {"num_ctx": self.num_ctx, "temperature": 0.2},
        }
        if self.reasoning_effort == "none":
            body["think"] = False
        if tools:
            body["tools"] = tools
        if json_schema:
            body["format"] = json_schema
        try:
            async with self.http.post(f"{self.base_url}/api/chat", json=body, timeout=self.timeout) as res:
                payload = await res.json(content_type=None)
                if res.status != 200:
                    raise LLMError(f"HTTP {res.status}: {json.dumps(payload, ensure_ascii=False)[:300]}")
        except (aiohttp.ClientError, TimeoutError) as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e
        return from_ollama_message(payload["message"])


def to_ollama_messages(messages: list[dict]) -> list[dict]:
    """OpenAI 형식 → Ollama 형식 (tool_calls 인자는 객체, tool 결과에는 tool_name)."""
    names: dict[str, str] = {}
    out = []
    for m in messages:
        m = dict(m)
        if m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                names[c.get("id", "")] = c["function"]["name"]
                calls.append({"function": {"name": c["function"]["name"], "arguments": _args(c)}})
            m["tool_calls"] = calls
        if m.get("role") == "tool":
            m["tool_name"] = names.get(m.pop("tool_call_id", ""), "")
        out.append(m)
    return out


def _args(call: dict) -> dict:
    args = call["function"].get("arguments") or {}
    if isinstance(args, dict):
        return args
    try:
        return json.loads(args)
    except json.JSONDecodeError:
        return {}  # 모델이 잘못된 JSON을 냈던 호출 — 기록만 남기고 인자는 비운다


def from_ollama_message(message: dict) -> dict:
    """Ollama 응답 → OpenAI 형식 assistant 메시지."""
    result = {"role": "assistant", "content": message.get("content") or ""}
    calls = message.get("tool_calls") or []
    if calls:
        result["tool_calls"] = [{
            "id": c.get("id") or f"call_{i}",
            "type": "function",
            "function": {
                "name": c["function"]["name"],
                "arguments": json.dumps(c["function"].get("arguments") or {}, ensure_ascii=False),
            },
        } for i, c in enumerate(calls)]
    return result


def make_llm(settings: Settings, http: aiohttp.ClientSession) -> LLMClient:
    return OllamaClient(settings, http) if settings.llm_provider == "ollama" else LLMClient(settings, http)


def parse_json(text: str) -> dict:
    """모델 출력에서 JSON 객체를 꺼낸다 (코드펜스·앞뒤 설명이 섞여 있어도 처리)."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])
