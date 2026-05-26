"""Port para provedores de LLM (OpenAI, Bedrock, etc.)."""

from typing import Any, Awaitable, Callable, Protocol


# Assinatura do executor de tools que o LLM pode chamar.
# É um simples async callable: (tool_name, arguments) -> tool_result
ToolExecutor = Callable[[str, dict], Awaitable[Any]]


class LLMProvider(Protocol):
    """Contrato para qualquer adapter que provê chat com function calling."""

    async def chat(
        self,
        user_message: str,
        tool_executor: ToolExecutor,
    ) -> str:
        """Enviar mensagem ao LLM, executar tools quando necessário, retornar resposta final."""
        ...

    def reset(self) -> None:
        """Limpar histórico da conversa."""
        ...
