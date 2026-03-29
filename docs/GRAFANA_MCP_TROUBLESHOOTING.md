# Grafana MCP Server - Troubleshooting

## Problema: "Input should be an object" Error

### Sintoma
Ao tentar usar o MCP server do Grafana via Kiro, você recebe o erro:
```
Input should be an object [type=model_type, input_value='...', input_type=str]
JSONRPCError
Internal Server Error
```

### Causa Raiz
Este erro ocorre quando há um problema na comunicação JSON-RPC entre o Kiro e o MCP server. Possíveis causas:

1. **Servidor MCP desatualizado**: O servidor foi modificado mas o Kiro ainda está usando a versão antiga em cache
2. **Problema de validação Pydantic**: O servidor está recebendo dados em formato incorreto
3. **Conexão MCP não reiniciada**: Após mudanças no código, o Kiro precisa reconectar

### Solução

#### Opção 1: Reiniciar Conexão MCP no Kiro (Recomendado)

1. Abra o painel lateral do Kiro (View → Kiro)
2. Procure pela seção "MCP Servers" 
3. Encontre o servidor "grafana"
4. Clique no botão de reconectar/restart ao lado do servidor
5. Aguarde a reconexão (deve mostrar status "connected")

#### Opção 2: Reiniciar o Kiro Completamente

Se a opção 1 não funcionar:
1. Feche o Kiro completamente
2. Reabra o Kiro
3. O servidor MCP será reiniciado automaticamente

#### Opção 3: Verificar Configuração

Verifique se `.kiro/settings/mcp.json` está correto:

```json
{
  "mcpServers": {
    "grafana": {
      "command": "python",
      "args": ["mcp-servers/grafana_v2.py"],
      "env": {
        "GRAFANA_URL": "http://127.0.0.1:3000/",
        "GRAFANA_TOKEN": "seu_token_aqui",
        "GRAFANA_ORG_ID": "1",
        "GRAFANA_TIMEOUT_S": "15"
      },
      "disabled": false
    }
  }
}
```

Certifique-se de que:
- `disabled` está como `false`
- As variáveis de ambiente estão corretas
- O token do Grafana é válido

#### Opção 4: Testar Servidor Diretamente

Para verificar se o servidor funciona independentemente do Kiro:

```bash
cd mcp-servers
export GRAFANA_URL="http://127.0.0.1:3000/"
export GRAFANA_TOKEN="seu_token_aqui"
export GRAFANA_ORG_ID="1"
export GRAFANA_TIMEOUT_S="15"

python test_server_direct.py
```

Se este teste funcionar, o problema está na comunicação Kiro ↔ MCP.

### Verificação de Sucesso

Após aplicar a solução, teste com uma pergunta simples:
```
"Quais alertas estão firing no Grafana?"
```

Você deve receber uma resposta estruturada com a lista de alertas (ou uma lista vazia se não houver alertas ativos).

## Logs de Debug

Para ver logs detalhados do servidor MCP:

1. O servidor já está configurado com logging em DEBUG
2. Os logs aparecem no stderr do processo
3. No Kiro, você pode ver os logs na seção "MCP Servers" → clique no servidor → "View Logs"

## Problemas Conhecidos

### 1. Token Expirado
**Sintoma**: `HTTP error 401: Unauthorized`
**Solução**: Gere um novo token no Grafana e atualize `.kiro/settings/mcp.json`

### 2. Grafana Não Acessível
**Sintoma**: `Connection refused` ou timeout
**Solução**: Verifique se o Grafana está rodando em `http://127.0.0.1:3000/`

### 3. MCP Module Not Found
**Sintoma**: `No module named 'mcp'`
**Solução**: 
```bash
cd mcp-servers
pip install mcp>=1.0.0
```

## Contato

Se o problema persistir após seguir este guia, documente:
1. Mensagem de erro completa
2. Versão do Kiro
3. Versão do Python (`python --version`)
4. Resultado de `pip list | grep mcp`
5. Conteúdo dos logs do servidor MCP
