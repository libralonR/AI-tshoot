#!/usr/bin/env python3
"""
Teste simples para o MCP server Grafana.
Verifica se o módulo pode ser importado e se as ferramentas estão definidas.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import grafana_v2
    print("✅ Módulo grafana_v2 importado com sucesso!")

    from grafana_v2 import GrafanaConfig
    print("✅ Classe GrafanaConfig encontrada")

    from grafana_v2 import GrafanaClient
    print("✅ Classe GrafanaClient encontrada")

    from grafana_v2 import app
    print("✅ Server MCP encontrado")

    from grafana_v2 import call_tool, list_tools
    print("✅ Funções call_tool e list_tools encontradas")

    from grafana_v2 import main_sse, main_stdio
    print("✅ Funções main_sse e main_stdio encontradas")

    print("\n✅ Todos os componentes do MCP server Grafana estão presentes!")
    print("\nFerramentas disponíveis:")
    print("1. get_alert_details - Buscar detalhes de alerta por UID")
    print("2. find_firing_alerts - Encontrar alertas firing por labels")
    print("3. find_dashboards - Encontrar dashboards por labels/tags")
    print("4. get_panel_link - Gerar link para painel com time range")

except ImportError as e:
    print(f"❌ Erro ao importar módulo: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Erro durante o teste: {e}")
    sys.exit(1)
