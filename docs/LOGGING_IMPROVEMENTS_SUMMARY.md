# Melhorias de Logging - Resumo Executivo

## Contexto

O orchestrator estava apresentando erro `openai.APITimeoutError: Request timed out` ao chamar o endpoint `/chat`, dificultando o diagnóstico do problema.

## Problema Identificado

Através dos logs detalhados adicionados, identificamos que o erro **NÃO é** um timeout de resposta da API, mas sim um **timeout de CONEXÃO TCP**:

```
httpcore.ConnectTimeout
httpx.ConnectTimeout  
openai.APITimeoutError: Request timed out.
```

**Causa raiz:** O orchestrator não consegue estabelecer conexão TCP com o LLM Gateway em:
```
https://genai-data-llm-gateway.ai.gondor.hom.infra/v1/chat/completions
```

**Evidências dos logs:**
- Múltiplas tentativas de retry (0.4s, 0.7s)
- Tempo total de ~16 segundos até falhar
- Erro na camada de conexão TCP, não na API

## Melhorias Implementadas

### 1. Logging Estruturado Completo

Adicionados logs detalhados em todos os componentes críticos:

#### `orchestrator/llm_client.py`
- ✅ Logs de inicialização (modelo, base_url, timeouts)
- ✅ Logs de cada chamada à API OpenAI (tempo, tokens, finish_reason)
- ✅ Logs de tool calls (nome, argumentos, tempo de execução)
- ✅ Logs de erros com tipo específico e mensagem completa
- ✅ Rastreamento de iterações de tool calling
- ✅ Tratamento específico para ConnectTimeout vs APITimeout

#### `orchestrator/orchestrator.py`
- ✅ Logs detalhados em `_gather_signals()` (tarefas paralelas, resultados)
- ✅ Logs em `_execute_tool()` (roteamento, tempo, tamanho resposta)
- ✅ Logs em `chat_endpoint()` (criação de sessão, tempo total)
- ✅ Correlação de chaves de incidentes (ci_name, inc_number)

#### `orchestrator/mcp_client.py`
- ✅ Logs de cada chamada MCP (endpoint, timeout, argumentos)
- ✅ Separação entre tempo total e tempo MCP (network overhead)
- ✅ Logs detalhados de erros HTTP (status code, response body)
- ✅ Tamanho da resposta em bytes

### 2. Timeouts Configuráveis

Adicionado suporte para configurar timeouts via variáveis de ambiente:

```python
# Em llm_client.py
timeout = float(os.getenv("OPENAI_TIMEOUT", "60"))
connect_timeout = float(os.getenv("OPENAI_CONNECT_TIMEOUT", "10"))

http_client = _httpx.AsyncClient(
    verify=False,
    timeout=_httpx.Timeout(
        timeout=timeout,
        connect=connect_timeout,
        read=timeout,
        write=10.0,
        pool=5.0
    )
)
```

**Variáveis de ambiente:**
- `OPENAI_TIMEOUT`: Timeout total (padrão: 60s)
- `OPENAI_CONNECT_TIMEOUT`: Timeout de conexão TCP (padrão: 10s)

### 3. Tratamento de Erros Melhorado

Mensagens de erro mais claras e acionáveis:

```python
# Antes
openai.APITimeoutError: Request timed out.

# Depois
RuntimeError: Não foi possível conectar ao LLM Gateway. 
Verifique conectividade de rede e DNS. 
URL: https://genai-data-llm-gateway.ai.gondor.hom.infra/v1/chat/completions
Erro: All connection attempts failed
```

### 4. Documentação de Troubleshooting

Criado guia completo: `docs/LLM_GATEWAY_TROUBLESHOOTING.md`

**Conteúdo:**
- Diagnóstico de problemas comuns (DNS, NetworkPolicy, Gateway down, Proxy)
- Comandos para verificação rápida
- Workarounds temporários
- Melhorias recomendadas
- Monitoramento e métricas

### 5. Script de Diagnóstico

Criado `orchestrator/diagnose_llm.py` para executar dentro do pod:

```bash
kubectl exec -it deployment/orchestrator -n copilot -- python3 diagnose_llm.py
```

**Testes executados:**
1. ✓ Resolução DNS
2. ✓ Conexão TCP
3. ✓ Conexão HTTPS
4. ✓ Cliente OpenAI (chamada real)
5. ✓ MCP Servers (Grafana, Incidents)

### 6. ConfigMap para Kubernetes

Criado `k8s/orchestrator/configmap-llm-timeout.yaml`:

```yaml
data:
  OPENAI_TIMEOUT: "120"
  OPENAI_CONNECT_TIMEOUT: "15"
  OPENAI_MODEL: "gpt-5.2-openai"
```

## Formato dos Logs

Todos os logs seguem o padrão estruturado:

```
[function_name] message | key=value | key2=value2
```

**Exemplo:**
```
[LLMClient.chat] OpenAI API response received | api_time=1.234s | finish_reason=stop | usage={'prompt_tokens': 100, 'completion_tokens': 50}
```

**Benefícios:**
- Fácil parsing para ferramentas de log (Splunk, ELK)
- Grep/filter eficiente
- Rastreamento de performance
- Correlação de eventos

## Próximos Passos Recomendados

### Imediato (Resolver o problema atual)

1. **Verificar conectividade de rede:**
   ```bash
   kubectl exec -it deployment/orchestrator -n copilot -- python3 diagnose_llm.py
   ```

2. **Verificar DNS:**
   ```bash
   kubectl exec -it deployment/orchestrator -n copilot -- \
     nslookup genai-data-llm-gateway.ai.gondor.hom.infra
   ```

3. **Testar conectividade TCP:**
   ```bash
   kubectl exec -it deployment/orchestrator -n copilot -- \
     nc -zv genai-data-llm-gateway.ai.gondor.hom.infra 443
   ```

4. **Verificar NetworkPolicy:**
   ```bash
   kubectl get networkpolicy -n copilot
   ```

5. **Aumentar timeouts temporariamente:**
   ```bash
   kubectl set env deployment/orchestrator -n copilot \
     OPENAI_TIMEOUT=120 \
     OPENAI_CONNECT_TIMEOUT=15
   ```

### Curto Prazo (Melhorias operacionais)

1. **Adicionar health check do LLM:**
   ```python
   @app.get("/health/llm")
   async def llm_health_check():
       # Testar conectividade com gateway
   ```

2. **Adicionar métricas Prometheus:**
   ```python
   llm_request_duration = Histogram('llm_request_duration_seconds')
   llm_request_errors = Counter('llm_request_errors_total', ['error_type'])
   ```

3. **Implementar circuit breaker:**
   - Usar `pybreaker` para evitar chamadas quando gateway está down
   - Fallback para modo degradado

4. **Configurar alertas:**
   - Alerta quando taxa de erro > 10%
   - Alerta quando latência > 10s

### Médio Prazo (Arquitetura)

1. **Adicionar cache de respostas:**
   - Redis para cachear respostas comuns
   - Reduzir dependência do gateway

2. **Implementar retry com backoff exponencial:**
   - Melhor que retry linear do OpenAI client

3. **Adicionar fallback para modelo local:**
   - Ollama ou similar para casos de emergência

4. **Implementar rate limiting:**
   - Proteger gateway de sobrecarga

## Arquivos Modificados

```
orchestrator/
├── llm_client.py          # ✅ Logs detalhados + timeouts configuráveis
├── orchestrator.py        # ✅ Logs em _gather_signals, _execute_tool, chat_endpoint
├── mcp_client.py          # ✅ Logs detalhados de chamadas MCP
└── diagnose_llm.py        # ✅ NOVO: Script de diagnóstico

docs/
├── LLM_GATEWAY_TROUBLESHOOTING.md  # ✅ NOVO: Guia de troubleshooting
└── LOGGING_IMPROVEMENTS_SUMMARY.md # ✅ NOVO: Este documento

k8s/orchestrator/
└── configmap-llm-timeout.yaml      # ✅ NOVO: ConfigMap com timeouts
```

## Exemplo de Uso dos Logs

### Diagnosticar timeout:

```bash
# Ver logs do orchestrator
kubectl logs -f deployment/orchestrator -n copilot | grep -E "\[LLMClient|\[chat_endpoint"

# Procurar por:
# 1. Inicialização
[LLMClient.__init__] Initialized | model=gpt-5.2-openai | base_url=https://... | timeout=60s

# 2. Início da chamada
[LLMClient.chat] Calling OpenAI API | model=gpt-5.2-openai | messages_count=2

# 3. Retries (indica problema)
Retrying request to /chat/completions in 0.457411 seconds

# 4. Erro específico
[LLMClient.chat] Connection timeout/error | error_type=ConnectTimeout | elapsed=16.245s
```

### Monitorar performance:

```bash
# Ver tempos de execução
kubectl logs deployment/orchestrator -n copilot | grep "execution_time"

# Exemplos:
[_gather_signals] Signal gathering completed | evidence_count=313 | execution_time=0.156s
[chat_endpoint] Chat completed successfully | execution_time=2.345s
[MCPClient.call_tool] MCP call completed | total_time=0.295s | mcp_execution_time=0.145s
```

## Contatos

- **Documentação:** `docs/LLM_GATEWAY_TROUBLESHOOTING.md`
- **Script de diagnóstico:** `orchestrator/diagnose_llm.py`
- **Issues:** Abrir issue no repositório com logs completos
