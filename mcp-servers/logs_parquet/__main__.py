"""
Entry point for `python -m logs_parquet`.

Detects MCP_SERVER_MODE and starts the appropriate server mode.
"""

import asyncio
import os

from .config import log
from .server import main_sse, main_stdio

if __name__ == "__main__":
    mode = os.getenv("MCP_SERVER_MODE", "sse").lower()
    log.info(f"Starting logs_parquet in mode={mode}")
    log.info(f"ENV: LOGS_S3_BUCKET={os.getenv('LOGS_S3_BUCKET', 'NOT SET')}")
    log.info(f"ENV: LOGS_AWS_REGION={os.getenv('LOGS_AWS_REGION', 'NOT SET')}")
    log.info(
        f"ENV: LOGS_ROLE_ARN={'SET' if os.getenv('LOGS_ROLE_ARN') else 'NOT SET (using default chain)'}"
    )
    log.info(f"ENV: MCP_SERVER_MODE={mode}")

    if mode == "sse":
        main_sse()
    else:
        asyncio.run(main_stdio())
