# Observability Troubleshooting Copilot (PoC)

## Propósito
Ajudar SREs e usuários internos a identificar rapidamente "onde está o problema" correlacionando métricas (VictoriaMetrics/PromQL),
logs (Splunk + S3 Parquet/Athena/Trino), traces (OTel + Tempo), alertas/dashboards (Grafana) e incidentes (ServiceNow).

## Público-alvo
- SRE/Observability
- On-call (plataforma/app)
- Time de Operações
- Times de produto (consulta guiada, sem acesso direto às ferramentas)

## Principais capacidades (PoC)
1. Entrada por: Incident ID (ServiceNow) OU Alert UID (Grafana) OU sintoma livre.
2. Geração de CaseFile (dossiê): escopo, janela de tempo, sinais, evidências, hipóteses.
3. Resposta com evidências: queries, traceIds, assinaturas de erro, links de dashboards/painéis/alertas.
4. Recomendações de próximos passos (somente ações seguras / read-only).

## Fora de escopo (agora)
- Execução automática de ações de remediação (restart, rollback, scale, etc.)
- Escrita/alteração em ServiceNow/Grafana (somente leitura)
- "Auto-fix" sem aprovação humana

## Métricas de sucesso
- Redução de MTTR e tempo de triagem
- Acurácia: hipótese principal com evidência rastreável
- Menos troca de contexto: links e queries prontas
