"""환경변수 기반 설정."""
import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    return int(_env(name) or default)


def _float(name: str, default: float) -> float:
    return float(_env(name) or default)


def _ids(value: str) -> frozenset[int]:
    return frozenset(int(v) for v in value.replace(" ", "").split(",") if v)


@dataclass(frozen=True)
class Settings:
    # 관측 대상
    prometheus_url: str = "http://localhost:9090"
    loki_url: str = "http://localhost:3100"
    app_url: str = "http://localhost:5001"
    chaos_admin_token: str = ""

    # 조치 대상
    docker_proxy_url: str = "http://localhost:2375"
    app_container: str = "FastApi-app"
    github_token: str = ""
    github_repo: str = ""            # "owner/repo"
    audit_log: str = "audit.jsonl"
    incident_log: str = "incidents.jsonl"   # KPI 계산용 인시던트 기록

    # LLM: ollama | gemini (둘 다 OpenAI 호환 Chat Completions API로 호출)
    llm_provider: str = "ollama"
    llm_model: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_reasoning_effort: str = ""
    llm_timeout: float = 180.0
    ollama_num_ctx: int = 16384      # Ollama 기본 컨텍스트(4K)로는 증거·도구 결과가 잘린다
    llm_max_steps: int = 6

    # Discord (토큰이 없으면 콘솔 모드로 동작)
    discord_token: str = ""
    discord_channel_id: int = 0
    discord_guild_id: int = 0        # 지정하면 슬래시 명령이 해당 서버에 즉시 등록된다
    discord_approver_role: str = ""
    discord_approver_ids: frozenset[int] = field(default_factory=frozenset)

    # 서버·검증
    http_port: int = 8080
    agent_api_token: str = ""        # 콘솔 모드 조치 API(/incidents/{id}/actions/{action}) 인증
    verify_delay: float = 30.0       # 조치 후 복구 검증까지 대기 시간(초)

    @classmethod
    def from_env(cls) -> "Settings":
        provider = _env("LLM_PROVIDER", "ollama").lower()
        if provider == "gemini":
            llm = dict(
                llm_model=_env("LLM_MODEL") or _env("GEMINI_MODEL", "gemini-flash-latest"),
                llm_base_url=_env("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"),
                llm_api_key=_env("GEMINI_API_KEY"),
                llm_reasoning_effort=_env("LLM_REASONING_EFFORT", "low"),
            )
        elif provider == "ollama":
            llm = dict(
                llm_model=_env("LLM_MODEL") or _env("OLLAMA_MODEL", "qwen3:8b"),
                llm_base_url=(_env("LLM_BASE_URL") or _env("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/"),
                llm_api_key="",
                llm_reasoning_effort=_env("LLM_REASONING_EFFORT", "none"),  # none이면 qwen3 thinking 끄기 (응답 속도)
            )
        else:
            raise ValueError(f"지원하지 않는 LLM_PROVIDER: {provider} (ollama | gemini)")

        return cls(
            prometheus_url=_env("PROMETHEUS_URL", cls.prometheus_url),
            loki_url=_env("LOKI_URL", cls.loki_url),
            app_url=_env("APP_URL", cls.app_url),
            chaos_admin_token=_env("CHAOS_ADMIN_TOKEN"),
            docker_proxy_url=_env("DOCKER_PROXY_URL", cls.docker_proxy_url),
            app_container=_env("APP_CONTAINER", cls.app_container),
            github_token=_env("GITHUB_TOKEN"),
            github_repo=_env("GITHUB_REPO"),
            audit_log=_env("AUDIT_LOG", cls.audit_log),
            incident_log=_env("INCIDENT_LOG", cls.incident_log),
            llm_provider=provider,
            llm_timeout=_float("LLM_TIMEOUT", cls.llm_timeout),
            llm_max_steps=_int("LLM_MAX_STEPS", cls.llm_max_steps),
            ollama_num_ctx=_int("OLLAMA_NUM_CTX", cls.ollama_num_ctx),
            discord_token=_env("DISCORD_TOKEN"),
            discord_channel_id=_int("DISCORD_CHANNEL_ID", 0),
            discord_guild_id=_int("DISCORD_GUILD_ID", 0),
            discord_approver_role=_env("DISCORD_APPROVER_ROLE"),
            discord_approver_ids=_ids(_env("DISCORD_APPROVER_IDS")),
            http_port=_int("HTTP_PORT", cls.http_port),
            agent_api_token=_env("AGENT_API_TOKEN"),
            verify_delay=_float("VERIFY_DELAY", cls.verify_delay),
            **llm,
        )

    @property
    def llm_label(self) -> str:
        return f"{self.llm_provider}/{self.llm_model}"
