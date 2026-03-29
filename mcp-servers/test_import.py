import sys
try:
    import mcp
    print(f'MCP importado com sucesso')
    print(f'Versão: {getattr(mcp, "__version__", "unknown")}')
    print(f'Módulos disponíveis: {[x for x in dir(mcp) if not x.startswith("_")]}')
except ImportError as e:
    print(f'Erro ao importar MCP: {e}')
    sys.exit(1)
