import json

from ops_agent.llm import from_ollama_message, to_ollama_messages


def test_openai_history_converted_for_ollama():
    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "query_prometheus", "arguments": '{"query": "up"}'}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "[]"},
    ]
    out = to_ollama_messages(history)
    assert out[1]["tool_calls"] == [{"function": {"name": "query_prometheus", "arguments": {"query": "up"}}}]
    assert out[2] == {"role": "tool", "content": "[]", "tool_name": "query_prometheus"}
    assert history[2]["tool_call_id"] == "c1"  # 원본은 건드리지 않는다


def test_ollama_response_converted_to_openai():
    msg = from_ollama_message({"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "search_logs", "arguments": {"query": "{x}"}}}
    ]})
    call = msg["tool_calls"][0]
    assert call["id"] == "call_0"
    assert json.loads(call["function"]["arguments"]) == {"query": "{x}"}
    assert from_ollama_message({"content": "hi"}) == {"role": "assistant", "content": "hi"}


def test_invalid_arguments_in_history_do_not_crash():
    out = to_ollama_messages([{"role": "assistant", "content": "", "tool_calls": [
        {"id": "x", "function": {"name": "query_prometheus", "arguments": "not json"}}]}])
    assert out[0]["tool_calls"][0]["function"]["arguments"] == {}
