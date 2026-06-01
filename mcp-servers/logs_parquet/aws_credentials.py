"""AWS credentials manager (AssumeRole + refresh)."""

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from .config import LogsConfig

log = logging.getLogger("logs-parquet-mcp")


class AWSCredentialsManager:
    """Gerencia credenciais AWS para acesso ao S3 via DuckDB.

    Estratégia:
      1. Se `LOGS_ROLE_ARN` definido → usa STS AssumeRole, refresh ~10min antes
         de expirar.
      2. Se IRSA (web identity token), boto3 detecta automaticamente.
      3. Senão, usa credenciais default da boto3 (env vars, instance profile,
         ~/.aws/credentials, etc).
    """

    REFRESH_MARGIN = timedelta(minutes=10)

    def __init__(self, cfg: LogsConfig):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._access_key: Optional[str] = None
        self._secret_key: Optional[str] = None
        self._session_token: Optional[str] = None
        self._expiration: Optional[datetime] = None

    def get(self) -> Dict[str, Optional[str]]:
        """Retorna dict com credenciais válidas, refrescando se necessário."""
        with self._lock:
            if self._needs_refresh():
                self._refresh()
            return {
                "access_key_id": self._access_key,
                "secret_access_key": self._secret_key,
                "session_token": self._session_token,
                "region": self.cfg.aws_region,
            }

    def _needs_refresh(self) -> bool:
        if self._access_key is None:
            return True
        if self._expiration is None:
            # Credencial estática (sem expiração) — não precisa refresh.
            return False
        return datetime.now(timezone.utc) + self.REFRESH_MARGIN >= self._expiration

    def _refresh(self) -> None:
        # boto3 é importado lazy para não ser obrigatório em modo offline.
        try:
            import boto3  # type: ignore
        except ImportError as exc:  # pragma: no cover - env should have boto3
            raise RuntimeError(
                "boto3 not installed; required for LOGS_ROLE_ARN AssumeRole"
            ) from exc

        if self.cfg.role_arn:
            log.info(
                f"[AWSCredentialsManager] AssumeRole role_arn={self.cfg.role_arn} "
                f"session={self.cfg.role_session_name}"
            )
            sts = boto3.client("sts", region_name=self.cfg.aws_region)
            resp = sts.assume_role(
                RoleArn=self.cfg.role_arn,
                RoleSessionName=self.cfg.role_session_name,
                DurationSeconds=int(os.getenv("LOGS_ROLE_DURATION_SECONDS", "3600")),
            )
            creds = resp["Credentials"]
            self._access_key = creds["AccessKeyId"]
            self._secret_key = creds["SecretAccessKey"]
            self._session_token = creds["SessionToken"]
            self._expiration = creds["Expiration"]
            log.info(
                f"[AWSCredentialsManager] credentials refreshed | "
                f"expires_at={self._expiration.isoformat()}"
            )
            return

        # Sem role_arn → tenta credenciais default da chain do boto3.
        session = boto3.Session(region_name=self.cfg.aws_region)
        creds = session.get_credentials()
        if creds is None:
            raise RuntimeError(
                "No AWS credentials available. Set LOGS_ROLE_ARN or configure "
                "default credential chain (IRSA, env vars, instance profile)."
            )
        frozen = creds.get_frozen_credentials()
        self._access_key = frozen.access_key
        self._secret_key = frozen.secret_key
        self._session_token = frozen.token
        # boto3 default chain pode não expor expiração — assume estático.
        self._expiration = None
        log.info(
            f"[AWSCredentialsManager] using default credential chain | "
            f"access_key=...{self._access_key[-4:] if self._access_key else 'None'}"
        )
