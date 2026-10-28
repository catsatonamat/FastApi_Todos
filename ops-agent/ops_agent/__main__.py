"""ops-agent 진입점: 웹훅 서버 + (토큰이 있으면) Discord 봇을 한 이벤트 루프에서 실행한다."""
import asyncio
import logging

import aiohttp
from aiohttp import web

from .actions import ActionExecutor
from .agent import Investigator
from .config import Settings
from .llm import make_llm
from .logging_setup import setup_logging
from .observability import Observability
from .service import OpsService
from .webhook import create_app

log = logging.getLogger("ops_agent")


async def main() -> None:
    setup_logging()
    settings = Settings.from_env()

    async with aiohttp.ClientSession() as http:
        obs = Observability(settings, http)
        investigator = Investigator(make_llm(settings, http), obs, settings.llm_label, settings.llm_max_steps)
        service = OpsService(settings, obs, investigator, ActionExecutor(settings, http))

        bot = None
        if settings.discord_token and settings.discord_channel_id:
            from .discord_bot import DiscordNotifier, OpsBot

            bot = OpsBot(settings)
            bot.service = service
            service.notifier = DiscordNotifier(bot)
            if not (settings.discord_approver_ids or settings.discord_approver_role):
                log.warning("승인자 제한이 없습니다 — 채널의 누구나 조치를 승인할 수 있습니다")
        else:
            log.warning("DISCORD_TOKEN/DISCORD_CHANNEL_ID 미설정 — 콘솔 모드로 실행합니다")

        runner = web.AppRunner(create_app(service))
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", settings.http_port).start()
        log.info("ops-agent started", extra={"port": settings.http_port, "llm": settings.llm_label,
                                             "discord": bot is not None})
        try:
            if bot:
                await bot.start(settings.discord_token)
            else:
                await asyncio.Event().wait()
        finally:
            if bot:
                await bot.close()
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
