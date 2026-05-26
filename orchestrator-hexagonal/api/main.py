"""Entrypoint da aplicação FastAPI (driving adapter)."""

import logging
import os
import sys

from fastapi import FastAPI

from api.http.routes import router
from infrastructure.config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("orchestrator")


app = FastAPI(
    title="Observability Troubleshooting Copilot (Hexagonal)",
    description="AI-powered incident triage and root cause analysis — Ports & Adapters",
    version="1.1.0-hex",
)
app.include_router(router)


@app.on_event("startup")
async def _startup():
    log.info("Starting Observability Troubleshooting Copilot Orchestrator (hexagonal)")
    log.info(f"Loaded {len(config.steering_context)} steering files")
    log.info(f"MCP servers configured: {list(config.mcp_servers.keys())}")
    log.info(f"Metrics catalog entries: {len(config.metrics_catalog)}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        log_level="info",
    )
