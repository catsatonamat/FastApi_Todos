from ops_agent.config import Settings


def test_empty_numeric_env_uses_defaults(monkeypatch):
    for name in ("DISCORD_CHANNEL_ID", "DISCORD_GUILD_ID", "VERIFY_DELAY", "LLM_MAX_STEPS"):
        monkeypatch.setenv(name, "")
    s = Settings.from_env()
    assert s.discord_channel_id == 0 and s.verify_delay == 30.0 and s.llm_max_steps == 6


def test_provider_switch(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    s = Settings.from_env()
    assert s.llm_base_url.startswith("https://generativelanguage.googleapis.com")
    assert s.llm_api_key == "k" and s.llm_model == "gemini-flash-latest"

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434/")
    s = Settings.from_env()
    assert s.llm_base_url == "http://host.docker.internal:11434" and s.llm_reasoning_effort == "none"
    assert s.ollama_num_ctx == 16384


def test_approver_ids_parsing(monkeypatch):
    monkeypatch.setenv("DISCORD_APPROVER_IDS", "1, 2,,3")
    assert Settings.from_env().discord_approver_ids == frozenset({1, 2, 3})
