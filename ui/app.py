"""
Observability Troubleshooting Copilot — Streamlit UI
Interface gráfica para testar e demonstrar o Copilot.
Consome os endpoints /chat e /investigate do orchestrator.
"""

import json
import os
import time

import httpx
import streamlit as st

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8080")

st.set_page_config(
    page_title="Observability Copilot",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/search-in-cloud.png", width=64)
    st.title("Observability Copilot")
    st.caption("Triagem inteligente de incidentes")

    st.divider()

    orchestrator_url = st.text_input(
        "Orchestrator URL",
        value=ORCHESTRATOR_URL,
        help="URL do orchestrator (ex: http://localhost:8080)",
    )

    # Health check
    try:
        r = httpx.get(f"{orchestrator_url}/health", timeout=5, verify=False)
        if r.status_code == 200:
            st.success("Orchestrator online", icon="✅")
        else:
            st.error(f"Status: {r.status_code}", icon="❌")
    except Exception:
        st.error("Orchestrator offline", icon="❌")

    st.divider()
    mode = st.radio("Modo", ["💬 Chat", "🔍 Investigate"], index=0)

    st.divider()
    st.caption("v1.1.0 — PoC")


# ---------------------------------------------------------------------------
# Chat Mode
# ---------------------------------------------------------------------------
def render_chat():
    st.header("💬 Chat com o Copilot")
    st.caption("Pergunte em linguagem natural. O Copilot busca alertas, incidentes e métricas automaticamente.")

    # Session state
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "session_id" not in st.session_state:
        st.session_state.session_id = None

    # Render history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Input
    if prompt := st.chat_input("Ex: Quais alertas estão abertos para grafana-tempo?"):
        # User message
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # Call orchestrator
        with st.chat_message("assistant"):
            with st.spinner("Analisando..."):
                start = time.time()
                try:
                    payload = {"message": prompt}
                    if st.session_state.session_id:
                        payload["session_id"] = st.session_state.session_id

                    r = httpx.post(
                        f"{orchestrator_url}/chat",
                        json=payload,
                        timeout=120,
                        verify=False,
                    )
                    elapsed = time.time() - start

                    if r.status_code == 200:
                        data = r.json()
                        response = data["response"]
                        st.session_state.session_id = data.get("session_id")

                        st.markdown(response)
                        st.caption(f"⏱️ {elapsed:.1f}s | session: `{st.session_state.session_id[:8]}...`")

                        st.session_state.messages.append({"role": "assistant", "content": response})
                    else:
                        st.error(f"Erro {r.status_code}: {r.text[:300]}")
                except httpx.TimeoutException:
                    st.error("Timeout — o orchestrator demorou mais de 120s para responder.")
                except Exception as e:
                    st.error(f"Erro: {e}")

    # Clear button
    col1, col2 = st.columns([6, 1])
    with col2:
        if st.button("🗑️ Limpar"):
            st.session_state.messages = []
            st.session_state.session_id = None
            st.rerun()


# ---------------------------------------------------------------------------
# Investigate Mode
# ---------------------------------------------------------------------------
def render_investigate():
    st.header("🔍 Investigate")
    st.caption("Análise estruturada sem LLM — coleta alertas, incidentes e métricas automaticamente.")

    col1, col2 = st.columns([1, 1])

    with col1:
        input_type = st.selectbox(
            "Tipo de entrada",
            ["SYMPTOM", "INCIDENT_ID", "ALERT_UID"],
            help="SYMPTOM: texto livre | INCIDENT_ID: INC0012345 | ALERT_UID: uid do alerta Grafana",
        )

        value = st.text_input(
            "Valor",
            placeholder="Ex: alertas do grafana-tempo" if input_type == "SYMPTOM" else "Ex: INC1386530",
        )

    with col2:
        st.subheader("Filtros (opcional)")
        app_svc = st.text_input("application_service", placeholder="Ex: grafana-tempo")
        biz_cap = st.text_input("business_capability", placeholder="Ex: technology-services")
        owner = st.text_input("owner_squad", placeholder="Ex: l-sre-observability")

    if st.button("🚀 Investigar", type="primary", disabled=not value):
        filters = {}
        if app_svc:
            filters["application_service"] = app_svc
        if biz_cap:
            filters["business_capability"] = biz_cap
        if owner:
            filters["owner_squad"] = owner

        payload = {
            "input_type": input_type,
            "value": value,
            "user": "streamlit-ui",
        }
        if filters:
            payload["filters"] = filters

        with st.spinner("Investigando..."):
            start = time.time()
            try:
                r = httpx.post(
                    f"{orchestrator_url}/investigate",
                    json=payload,
                    timeout=60,
                    verify=False,
                )
                elapsed = time.time() - start

                if r.status_code == 200:
                    data = r.json()
                    render_casefile(data, elapsed)
                else:
                    st.error(f"Erro {r.status_code}: {r.text[:500]}")
            except httpx.TimeoutException:
                st.error("Timeout — investigação demorou mais de 60s.")
            except Exception as e:
                st.error(f"Erro: {e}")


def render_casefile(data: dict, elapsed: float):
    """Renderiza o CaseFile retornado pelo /investigate."""

    # Header
    st.success(f"Investigação concluída em {elapsed:.1f}s", icon="✅")

    # Scope
    scope = data.get("scope", {})
    cols = st.columns(4)
    cols[0].metric("application_service", scope.get("serviceName", "—"))
    cols[1].metric("environment", scope.get("environment", "—"))
    cols[2].metric("cluster", scope.get("cluster", "—"))
    cols[3].metric("Evidências", len(data.get("evidence", [])))

    st.divider()

    # Tabs
    tab_alerts, tab_incidents, tab_hypotheses, tab_raw = st.tabs(
        ["🔔 Alertas", "🧯 Incidentes", "🔍 Hipóteses", "📄 JSON"]
    )

    evidence = data.get("evidence", [])
    alerts = [e for e in evidence if e.get("type") == "ALERT_FIRING"]
    incidents = [e for e in evidence if e.get("type") == "INCIDENT_RELATED"]

    # Alerts tab
    with tab_alerts:
        if alerts:
            st.subheader(f"🔔 Alertas Firing ({len(alerts)})")
            for a in alerts:
                result = a.get("result", {})
                labels = result.get("labels", {}) or result.get("correlation", {})
                alertname = labels.get("alertname", "—")
                app = labels.get("application_service", "—")
                cap = labels.get("business_capability", "—")
                link = a.get("links", [""])[0] if a.get("links") else ""

                snow = result.get("servicenow", {})
                kb = snow.get("kb", "") if snow else ""

                with st.expander(f"**{alertname}** — `{app}`"):
                    c1, c2, c3 = st.columns(3)
                    c1.write(f"**business_capability**: `{cap}`")
                    c2.write(f"**owner_squad**: `{labels.get('owner_squad', '—')}`")
                    c3.write(f"**Severidade**: `{labels.get('Severidade') or labels.get('severidade', '—')}`")

                    if link:
                        st.markdown(f"🔗 [Abrir no Grafana]({link})")
                    if kb:
                        kb_link = snow.get("kb_link", "")
                        if kb_link:
                            st.markdown(f"📖 KB: [{kb}]({kb_link})")
                        else:
                            st.markdown(f"📖 KB: `{kb}`")
        else:
            st.info("Nenhum alerta encontrado.")

    # Incidents tab
    with tab_incidents:
        if incidents:
            st.subheader(f"🧯 Incidentes Relacionados ({len(incidents)})")
            for inc in incidents[:20]:
                result = inc.get("result", {})
                number = result.get("number", "—")
                short = result.get("short_description", "—")[:80]
                priority = result.get("priority", "—")
                state = result.get("state", "—")
                opened = result.get("opened_at", "—")
                gl = result.get("_grafana_labels", {})
                app = gl.get("application_service", result.get("cmdb_ci_name", "—"))

                with st.expander(f"**{number}** — P{priority} — `{app}` — {short}"):
                    c1, c2, c3 = st.columns(3)
                    c1.write(f"**state**: `{state}`")
                    c2.write(f"**assignment_group**: `{result.get('assignment_group_name', '—')}`")
                    c3.write(f"**opened_at**: `{opened}`")

                    parsed = result.get("_parsed", {})
                    if parsed.get("origin_url"):
                        st.markdown(f"🔗 [Dashboard Origin]({parsed['origin_url']})")
                    if parsed.get("panel_url"):
                        st.markdown(f"📊 [Panel URL]({parsed['panel_url']})")
                    if parsed.get("alert_rule_uid"):
                        st.write(f"**alert_rule_uid**: `{parsed['alert_rule_uid']}`")

            if len(incidents) > 20:
                st.caption(f"Mostrando 20 de {len(incidents)} incidentes.")
        else:
            st.info("Nenhum incidente encontrado.")

    # Hypotheses tab
    with tab_hypotheses:
        hypotheses = data.get("hypotheses", [])
        if hypotheses:
            for h in hypotheses:
                conf = h.get("confidence", 0)
                comp = h.get("suspectedComponent", "—")
                cause = h.get("rootCause", "—")

                st.subheader(f"🔍 {comp} — Confidence: {conf:.0%}")
                st.write(f"**Root Cause**: {cause}")
                st.write(f"**Evidências**: {len(h.get('evidenceIds', []))}")

                steps = h.get("nextSteps", [])
                if steps:
                    st.write("**Próximos passos:**")
                    for s in steps:
                        prio = s.get("priority", "MEDIUM")
                        icon = "🔴" if prio == "HIGH" else "🟡"
                        action = s.get("action", "—")
                        query = s.get("query", "")
                        link = s.get("link", "")

                        text = f"{icon} **[{prio}]** {action}"
                        if link:
                            text += f" — [link]({link})"
                        st.markdown(text)
                        if query:
                            st.code(query, language="promql")
        else:
            st.info("Nenhuma hipótese gerada.")

    # Gaps
    gaps = data.get("correlationGaps", [])
    if gaps:
        st.divider()
        st.subheader("⚠️ Gaps de Correlação")
        for g in gaps:
            st.warning(
                f"**{g.get('missingLabel', '—')}** — {g.get('impact', '')} "
                f"| Recomendação: {g.get('recommendation', '')}"
            )

    # Raw JSON tab
    with tab_raw:
        st.json(data)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if mode == "💬 Chat":
    render_chat()
else:
    render_investigate()
