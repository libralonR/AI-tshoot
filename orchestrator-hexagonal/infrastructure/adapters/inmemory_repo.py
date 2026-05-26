"""Repositório in-memory para CaseFile.

Mantém o paralelo com a versão atual (sem persistência distribuída).
A migração futura para PostgreSQL/DynamoDB substitui apenas este adapter.
"""

import asyncio
from typing import Dict, Optional

from domain.models import CaseFile


class InMemoryCaseFileRepository:
    """CaseFileRepository implementation (in-memory dict, thread-safe)."""

    def __init__(self):
        self._store: Dict[str, CaseFile] = {}
        self._lock = asyncio.Lock()

    async def save(self, case_file: CaseFile) -> None:
        async with self._lock:
            self._store[case_file.id] = case_file

    async def get(self, case_file_id: str) -> Optional[CaseFile]:
        async with self._lock:
            return self._store.get(case_file_id)
