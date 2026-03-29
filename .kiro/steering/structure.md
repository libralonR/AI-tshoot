# Estrutura do repositório

- .kiro/steering/: contexto persistente (produto/stack/estrutura + arquivos específicos)
- .kiro/settings/mcp.json: configuração dos MCP servers (workspace-level)
- .kiro/agents/: agentes custom do Kiro CLI (compartilháveis via git)
- .kiro/prompts/: prompts longos referenciados por file:// nas configs dos agentes
- mcp-servers/: implementações (ou wrappers) MCP por fonte (grafana/vm/splunk/tempo/snow/athena)
- runbooks/: runbooks e padrões (RAG)
- docs/: documentação de arquitetura/contratos

Padrão: tudo que o agente usar deve estar documentado em steering/runbooks ou em specs.
