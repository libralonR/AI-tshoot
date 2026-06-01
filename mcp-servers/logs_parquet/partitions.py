"""
Partition helpers — time parsing, glob building, and filtering.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

from .config import MAX_WINDOW_HOURS, MAX_PARTITIONS, log

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _column_quoted(name: str) -> str:
    """Envolve nomes com hífen em aspas duplas para DuckDB."""
    return f'"{name}"' if "-" in name else name


# ---------------------------------------------------------------------------
# Partition helpers
# ---------------------------------------------------------------------------

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _parse_time(value) -> datetime:
    """Aceita ISO 8601 (com ou sem timezone) ou epoch_ms."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    s = str(value).strip()
    if s.isdigit():
        return datetime.fromtimestamp(int(s) / 1000.0, tz=timezone.utc)
    # Aceita "Z" e offset com colon
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"Invalid time format: {value!r} (expected ISO 8601 or epoch_ms)") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hours_in_range(start: datetime, end: datetime) -> List[datetime]:
    """Lista de horas (UTC) cobrindo o intervalo [start, end].

    Sempre inclui a hora de start e end. Falha se exceder MAX_PARTITIONS.
    """
    if end < start:
        raise ValueError("end must be >= start")
    if (end - start) > timedelta(hours=MAX_WINDOW_HOURS):
        raise ValueError(
            f"Time window exceeds limit of {MAX_WINDOW_HOURS}h. "
            f"start={start.isoformat()} end={end.isoformat()}"
        )
    cursor = start.replace(minute=0, second=0, microsecond=0)
    end_floor = end.replace(minute=0, second=0, microsecond=0)
    hours = []
    while cursor <= end_floor:
        hours.append(cursor)
        cursor += timedelta(hours=1)
    if len(hours) > MAX_PARTITIONS:
        raise ValueError(
            f"Time window expands to {len(hours)} partitions, max is {MAX_PARTITIONS}"
        )
    return hours


def _build_partition_globs(
    bucket: str,
    capabilities: Iterable[str],
    hours: Iterable[datetime],
) -> List[str]:
    """Gera lista de globs s3://bucket/capability=X/year=.../*.parquet."""
    caps = [c.strip() for c in capabilities if c and c.strip()]
    if not caps:
        # Sem capability conhecida → wildcard (busca cara, mas legítima).
        caps = ["*"]
    globs = []
    for cap in caps:
        cap_part = "*" if cap == "*" else f"capability={cap}"
        for h in hours:
            glob = (
                f"s3://{bucket}/{cap_part}/"
                f"year={h.year:04d}/month={h.month:02d}/day={h.day:02d}/hour={h.hour:02d}/*.parquet"
            )
            globs.append(glob)
    return globs


def _normalize_capabilities(business_capability: Optional[str]) -> List[str]:
    """Normaliza para lista (suporta CSV). Retorna lista vazia se None."""
    if not business_capability:
        return []
    return [c.strip() for c in business_capability.split(",") if c.strip()]


def _filter_existing_globs(
    globs: List[str],
    aws_region: Optional[str] = None,
    aws_credentials: Optional[dict] = None,
) -> List[str]:
    """Remove globs que não casam com nenhum arquivo.

    - Para `file://` ou paths absolutos: usa glob.glob.
    - Para `s3://`: usa boto3 list_objects_v2 (apenas se boto3 disponível).
    - Em caso de erro/timeout no S3, retorna o glob original (DuckDB tenta).

    Args:
        globs: lista de paths/URIs para filtrar.
        aws_region: região AWS para o cliente S3 (opcional).
        aws_credentials: dict com access_key_id/secret_access_key/session_token (opcional).
    """
    import glob as glob_module

    surviving: List[str] = []
    s3_globs: List[str] = []
    for g in globs:
        if g.startswith("s3://"):
            s3_globs.append(g)
        else:
            if glob_module.glob(g):
                surviving.append(g)

    if s3_globs:
        try:
            import boto3  # type: ignore

            creds = aws_credentials or {}
            session = boto3.Session(
                aws_access_key_id=creds.get("access_key_id"),
                aws_secret_access_key=creds.get("secret_access_key"),
                aws_session_token=creds.get("session_token"),
                region_name=creds.get("region", aws_region),
            )
            s3 = session.client("s3")
            for g in s3_globs:
                # s3://bucket/path/with/year=Y/month=M/.../*.parquet
                # remove o "*.parquet" final e usa o restante como Prefix
                prefix = g.replace("s3://", "", 1)
                bucket, _, key = prefix.partition("/")
                key_prefix = key.rsplit("/", 1)[0] + "/"
                try:
                    resp = s3.list_objects_v2(
                        Bucket=bucket, Prefix=key_prefix, MaxKeys=1
                    )
                    if resp.get("KeyCount", 0) > 0:
                        surviving.append(g)
                    else:
                        log.debug(f"[_filter_existing_globs] S3 prefix vazio: {key_prefix}")
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        f"[_filter_existing_globs] S3 list_objects_v2 falhou em {key_prefix}: {exc}; "
                        f"mantendo glob (DuckDB vai tentar)"
                    )
                    surviving.append(g)
        except ImportError:
            # Sem boto3 → mantém todos (DuckDB lança erro se vazio).
            surviving.extend(s3_globs)

    return surviving


def _build_globs_for_call(
    business_capability: Optional[str],
    start_dt: datetime,
    end_dt: datetime,
    bucket: str,
    aws_region: Optional[str] = None,
    aws_credentials: Optional[dict] = None,
) -> List[str]:
    """Monta a lista final de globs para uma chamada, já filtrando vazios.

    Args:
        business_capability: capability(ies) — string ou CSV. None ou "" → wildcard.
        start_dt, end_dt: janela de busca (datetime UTC).
        bucket: nome do bucket S3.
        aws_region: região AWS (passada ao boto3).
        aws_credentials: credenciais AWS (passadas ao boto3).
    """
    caps = _normalize_capabilities(business_capability) or ["*"]
    hours = _hours_in_range(start_dt, end_dt)
    raw = _build_partition_globs(bucket, caps, hours)
    surviving = _filter_existing_globs(raw, aws_region=aws_region, aws_credentials=aws_credentials)
    log.info(
        f"[_build_globs_for_call] caps={caps} | hours={len(hours)} | "
        f"raw_globs={len(raw)} | surviving={len(surviving)}"
    )
    return surviving


def _read_parquet_clause(globs: List[str]) -> str:
    """Monta o clause `read_parquet([...], hive_partitioning=true)`.

    DuckDB lança erro se algum glob não casar com nenhum arquivo. Em
    janelas que cobrem o "agora" pode haver hour ainda sem dados, e em
    capabilities que não geraram log na janela. O caller deve filtrar
    globs vazios antes (via boto3 list_objects_v2 para S3 ou os.path
    para file://). Quando a lista resulta vazia, retornamos um SELECT
    sintético vazio para preservar o shape do resultado.
    """
    if not globs:
        return (
            "(SELECT NULL::VARCHAR AS \"time\", NULL::VARCHAR AS \"level\", "
            "NULL::VARCHAR AS \"message\", NULL::VARCHAR AS \"business-capability\", "
            "NULL::VARCHAR AS \"business-domain\", NULL::VARCHAR AS \"business-service\", "
            "NULL::VARCHAR AS \"application-service\", NULL::VARCHAR AS \"args\", "
            "NULL::VARCHAR AS \"extra-fields\" WHERE 1=0)"
        )
    quoted = ", ".join(f"'{g}'" for g in globs)
    return f"read_parquet([{quoted}], hive_partitioning=true, union_by_name=true)"
