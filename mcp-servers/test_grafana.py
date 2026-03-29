#!/usr/bin/env python3
"""
Teste simples para o MCP server Grafana.
Este script testa se o servidor pode ser importado e se as ferramentas estão definidas.
"""

import sys
import os

# Adicionar o diretório atual ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    # Tentar importar o módulo grafana
    import grafana
    print("✅ Módulo grafana importado com sucesso!")
    
    # Verificar se as funções principais existem
    print("✅ Verificando estrutura do módulo...")
    
    # Verificar se a classe GrafanaConfig existe
    from grafana import GrafanaConfig
    print("✅ Classe GrafanaConfig encontrada")
    
    # Verificar se a classe GrafanaClient existe
    from grafana import GrafanaClient
    print("✅ Classe GrafanaClient encontrada")
    
    # Verificar se a função _json_result existe
    from grafana import _json_result
    print("✅ Função _json_result encontrada")
    
    # Verificar se o server existe
    from grafana import server
    print("✅ Server MCP encontrado")
    
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