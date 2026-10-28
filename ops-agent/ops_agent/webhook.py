"""HTTP 서버: Alertmanager 웹훅 수신 + 콘솔 모드용 인시던트 조회·조치 API."""
import logging
import secrets

from aiohttp import web
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

from .evaluation import KpiCollector
from .service import OpsService

log = logging.getLogger("ops_agent.webhook")


def create_app(service: OpsService) -> web.Application:
    routes = web.RouteTableDef()

    @routes.get("/healthz")
    async def healthz(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    @routes.post("/webhook/alertmanager")
    async def alertmanager(request: web.Request) -> web.Response:
        payload = await request.json()
        log.info(
            "alert received",
            extra={"status": payload.get("status"), "alerts": len(payload.get("alerts", [])),
                   "alertname": payload.get("commonLabels", {}).get("alertname")},
        )
        created = await service.handle_alertmanager(payload)
        return web.json_response({"created": created})

    registry = CollectorRegistry()
    registry.register(KpiCollector(service.store))

    @routes.get("/metrics")
    async def metrics(request: web.Request) -> web.Response:
        """Prometheus가 수집하는 에이전트 KPI (Grafana 'AIOps KPI' 패널)."""
        return web.Response(body=generate_latest(registry), headers={"Content-Type": CONTENT_TYPE_LATEST})

    @routes.get("/stats")
    async def stats(request: web.Request) -> web.Response:
        return web.json_response({"summary": service.store.summary(), "incidents": list(service.store.records.values())})

    @routes.get("/incidents")
    async def incidents(request: web.Request) -> web.Response:
        return web.json_response([i.to_dict() for i in service.recent(20)])

    @routes.get("/incidents/{id}")
    async def incident(request: web.Request) -> web.Response:
        inc = service.incidents.get(request.match_info["id"])
        if inc is None:
            raise web.HTTPNotFound()
        return web.json_response(inc.to_dict())

    @routes.post("/incidents/{id}/actions/{action}")
    async def run_action(request: web.Request) -> web.Response:
        """Discord 없이 조치를 승인할 때 사용 (AGENT_API_TOKEN 필요)."""
        token = service.s.agent_api_token
        given = request.headers.get("X-Agent-Token", "")
        if not token:
            raise web.HTTPNotFound()
        if not secrets.compare_digest(given, token):
            raise web.HTTPUnauthorized()
        actor = request.headers.get("X-Actor", "console")
        result = await service.run_action(request.match_info["id"], request.match_info["action"], actor)
        return web.json_response({"ok": result.ok, "detail": result.detail, "link": result.link},
                                 status=200 if result.ok else 400)

    app = web.Application()
    app.add_routes(routes)
    return app
