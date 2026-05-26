"""Caso de uso: chat conversacional via LLM com function calling.

Recebe um `LLMProvider` e um `tool_executor` (o roteador entre as tools do
LLM e os adapters concretos). Mantém o histórico em memória (sessão).
"""

import logging
from typing import Dict

from application.ports import LLMProvider, ToolExecutor

log = logging.getLogger("orchestrator")


class ChatUseCase:
    """Sessão única de chat. Cada session_id mapeia para uma instância."""

    def __init__(self, llm: LLMProvider):
        self.llm = llm

    async def execute(self, user_message: str, tool_executor: ToolExecutor) -> str:
        return await self.llm.chat(user_message=user_message, tool_executor=tool_executor)

    def reset(self) -> None:
        self.llm.reset()


class ChatSessionRegistry:
    """Registry simples de sessões de chat (in-memory).

    Mantém o paralelo com `_chat_sessions` da versão atual; quando precisarmos
    distribuir, basta trocar este componente por um Redis-backed sem mexer no
    use case.
    """

    def __init__(self):
        self._sessions: Dict[str, ChatUseCase] = {}

    def get_or_create(self, session_id: str, factory) -> ChatUseCase:
        if session_id not in self._sessions:
            self._sessions[session_id] = factory()
        return self._sessions[session_id]

    def has(self, session_id: str) -> bool:
        return session_id in self._sessions

    def drop(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
