# Observability Copilot — Streamlit UI

Interface gráfica para testar e demonstrar o Copilot.

## Quick Start

```bash
cd ui
pip install -r requirements.txt
streamlit run app.py
```

Abre em `http://localhost:8501`.

## Configuração

| Variável | Default | Descrição |
|----------|---------|-----------|
| `ORCHESTRATOR_URL` | `http://localhost:8080` | URL do orchestrator |

```bash
ORCHESTRATOR_URL=http://orchestrator:8080 streamlit run app.py
```

## Funcionalidades

### 💬 Chat
- Conversa em linguagem natural com o Copilot
- Renderiza markdown (tabelas, emojis, links)
- Mantém sessão (histórico de conversa)
- Timeout de 120s para respostas do LLM

### 🔍 Investigate
- Formulário com tipo de entrada (SYMPTOM, INCIDENT_ID, ALERT_UID)
- Filtros opcionais (application_service, business_capability, owner_squad)
- Renderização visual do CaseFile:
  - Métricas de scope (application_service, environment, cluster)
  - Tabs: Alertas, Incidentes, Hipóteses, JSON raw
  - Alertas com links Grafana e KB
  - Incidentes com links de dashboard/panel
  - Hipóteses com confidence e próximos passos (queries PromQL)
  - Gaps de correlação

## Docker

```bash
docker build -t copilot-ui -f Dockerfile .
docker run -p 8501:8501 -e ORCHESTRATOR_URL=http://host.docker.internal:8080 copilot-ui
```
