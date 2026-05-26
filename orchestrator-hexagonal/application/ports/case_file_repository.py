"""Port para persistência de CaseFiles."""

from typing import Optional, Protocol

from domain.models import CaseFile


class CaseFileRepository(Protocol):
    """Contrato para qualquer adapter de armazenamento de CaseFile.

    Implementações típicas:
      - InMemoryCaseFileRepository (PoC)
      - PgCaseFileRepository (futuro)
      - DynamoCaseFileRepository (futuro)
    """

    async def save(self, case_file: CaseFile) -> None:
        ...

    async def get(self, case_file_id: str) -> Optional[CaseFile]:
        ...
