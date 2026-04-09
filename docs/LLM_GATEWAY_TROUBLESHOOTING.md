# LLM Gateway Troubleshooting Guide

## Problema: APITimeoutError ao chamar /chat

### Sintomas
```
openai.APITimeoutError: Request timed out.
httpcore.ConnectTimeout
```

Logs mostram:
- `Retrying request to /chat/completions` (múltiplas tentativas)
- Erro após ~16 segundos
- `httpcore.ConnectTimeout` → falha na conexão TCP

### Diagnóstico

O erro **NÃO é** timeout de resposta da API, mas sim **timeout de CONEXÃO TCP**.

O orchestrator não consegue estabelecer conexão com:
```
https://genai-data-llm-gateway.ai.gondor.hom.infra/v1/chat/completions
```

### Possíveis Causas

#### 1. Problema de DNS
O hostname não está resolvendo dentro do cluster.

**Teste:**
```bash
# Dentro do pod do orchestrator
kubectl exec -it deployment/orchestrator -n copilot -- nslookup genai-data-llm-gateway.ai.gondor.hom.infra
```

**Solução:**
- Verificar se o DNS está configurado corretamente no cluster
- Adicionar entrada no CoreDNS se necessário
- Usar IP direto temporariamente para testar

#### 2. NetworkPolicy bloqueando
O namespace `copilot` pode não ter permissão para acessar o gateway.

**Teste:**
```bash
# Dentro do pod do orchestrator
kubectl exec -it deployment/orchestrator -n copilot -- curl -v https://genai-data-llm-gateway.ai.gondor.hom.infra/v1/chat/completions
```

**Solução:**
Criar NetworkPolicy permitindo egress:
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: orchestrator-egress-llm
  namespace: copilot
spec:
  podSelector:
    matchLabels:
      app: orchestrator
  policyTypes:
  - Egress
  egress:
  - to:
    - namespaceSelector: {}
    ports:
    - protocol: TCP
      port: 443
```

#### 3. Gateway indisponível
O serviço LLM Gateway pode estar down ou com problemas.

**Teste:**
```bash
# De fora do cluster
curl -v https://genai-data-llm-gateway.ai.gondor.hom.infra/health
```

**Solução:**
- Verificar status do gateway com time responsável
- Verificar logs do gateway
- Usar gateway alternativo temporariamente

#### 4. Proxy corporativo
Pode haver proxy HTTP/HTTPS bloqueando.

**Solução:**
Configurar proxy no orchestrator:
```yaml
env:
- name: HTTP_PROXY
  value: "http://proxy.corp:8080"
- name: HTTPS_PROXY
  value: "http://proxy.corp:8080"
- name: NO_PROXY
  value: ".svc.cluster.local,localhost,127.0.0.1"
```

#### 5. Timeout muito curto
O timeout padrão do OpenAI client pode ser muito curto.

**Solução:**
Aumentar timeout via variável de ambiente:
```yaml
env:
- name: OPENAI_TIMEOUT
  value: "60"  # segundos
```

### Verificações Rápidas

#### 1. Testar conectividade básica
```bash
# Dentro do pod
kubectl exec -it deployment/orchestrator -n copilot -- sh

# Testar DNS
nslookup genai-data-llm-gateway.ai.gondor.hom.infra

# Testar conectividade TCP
nc -zv genai-data-llm-gateway.ai.gondor.hom.infra 443

# Testar HTTPS
curl -v https://genai-data-llm-gateway.ai.gondor.hom.infra/
```

#### 2. Verificar logs do orchestrator
```bash
kubectl logs -f deployment/orchestrator -n copilot | grep -E "\[LLMClient|\[chat_endpoint"
```

Procurar por:
- `[LLMClient.__init__] Initialized | base_url=...` → confirmar URL
- `[LLMClient.chat] Calling OpenAI API` → início da chamada
- `Retrying request to /chat/completions` → retries (indica problema de conexão)
- `httpcore.ConnectTimeout` → falha de conexão TCP

#### 3. Verificar configuração
```bash
kubectl get secret orchestrator-secrets -n copilot -o yaml
```

Verificar:
- `OPENAI_BASE_URL` está correto
- `OPENAI_API_KEY` está presente
- `OPENAI_MODEL` está configurado

### Workarounds Temporários

#### 1. Usar modelo local (se disponível)
```yaml
env:
- name: OPENAI_BASE_URL
  value: "http://ollama.ai.svc.cluster.local:11434/v1"
- name: OPENAI_MODEL
  value: "llama3"
```

#### 2. Usar OpenAI direto (apenas para teste)
```yaml
env:
- name: OPENAI_BASE_URL
  value: "https://api.openai.com/v1"
- name: OPENAI_API_KEY
  value: "sk-..."
```

#### 3. Aumentar timeout
```yaml
env:
- name: OPENAI_TIMEOUT
  value: "120"  # 2 minutos
```

### Melhorias Recomendadas

#### 1. Adicionar health check do LLM
Criar endpoint `/health/llm` que testa conectividade:
```python
@app.get("/health/llm")
async def llm_health_check():
    try:
        # Testar conexão básica
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(os.getenv("OPENAI_BASE_URL"))
            return {"status": "healthy", "gateway": "reachable"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}
```

#### 2. Configurar timeout via env var
```python
# Em llm_client.py
timeout = float(os.getenv("OPENAI_TIMEOUT", "60"))
http_client = _httpx.AsyncClient(
    verify=False,
    timeout=httpx.Timeout(timeout=timeout, connect=10.0)
)
```

#### 3. Adicionar circuit breaker
Usar biblioteca como `pybreaker` para evitar chamadas repetidas quando gateway está down.

#### 4. Fallback para modo degradado
Se LLM não estiver disponível, retornar resposta básica sem IA:
```python
try:
    response = await llm.chat(...)
except APITimeoutError:
    return "LLM indisponível. Use /investigate para análise estruturada."
```

### Monitoramento

Adicionar métricas Prometheus:
```python
llm_request_duration = Histogram('llm_request_duration_seconds', 'LLM request duration')
llm_request_errors = Counter('llm_request_errors_total', 'LLM request errors', ['error_type'])
```

### Contatos

- **Time LLM Gateway:** #llm-gateway-support
- **Time Infra/Rede:** #infra-network
- **Documentação Gateway:** https://wiki.corp/llm-gateway
