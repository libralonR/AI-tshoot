#!/usr/bin/env python3
"""Pré-instala extensões DuckDB durante o docker build.

Roda como root no build (antes do USER mcp). Salva extensões em
/app/.duckdb_extensions/ que será depois acessível pelo user mcp via
ENV DUCKDB_EXTENSION_DIRECTORY.

Imprime mensagens claras para facilitar debug do build.
"""
import os
import sys

EXT_DIR = "/app/.duckdb_extensions"

print(f"[install_extensions] Starting | EXT_DIR={EXT_DIR}", flush=True)

# 1. Criar diretório (DuckDB não cria automaticamente)
os.makedirs(EXT_DIR, exist_ok=True)
print(f"[install_extensions] mkdir -p {EXT_DIR} OK", flush=True)

# 2. Importar duckdb
try:
    import duckdb  # noqa: E402
except ImportError as e:
    print(f"[install_extensions] FATAL: duckdb not installed: {e}", flush=True)
    sys.exit(1)

print(f"[install_extensions] duckdb version: {duckdb.__version__}", flush=True)

# 3. Conectar e configurar extension directory
conn = duckdb.connect()
try:
    conn.execute(f"SET extension_directory='{EXT_DIR}'")
    print(f"[install_extensions] SET extension_directory OK", flush=True)
except Exception as e:
    print(f"[install_extensions] FATAL: SET extension_directory failed: {e}", flush=True)
    sys.exit(1)

# 4. Instalar httpfs
try:
    conn.execute("INSTALL httpfs")
    print(f"[install_extensions] INSTALL httpfs OK", flush=True)
except Exception as e:
    print(f"[install_extensions] FATAL: INSTALL httpfs failed: {e}", flush=True)
    print(
        f"[install_extensions] HINT: this likely means no internet access during build. "
        f"Check corporate proxy or build with --network=host",
        flush=True,
    )
    sys.exit(1)

# 5. Validar carregamento
try:
    conn.execute("LOAD httpfs")
    print(f"[install_extensions] LOAD httpfs OK", flush=True)
except Exception as e:
    print(f"[install_extensions] FATAL: LOAD httpfs failed after install: {e}", flush=True)
    sys.exit(1)

conn.close()

# 6. Listar conteúdo do diretório para confirmar
print(f"[install_extensions] Contents of {EXT_DIR}:", flush=True)
for root, dirs, files in os.walk(EXT_DIR):
    for f in files:
        full = os.path.join(root, f)
        size = os.path.getsize(full)
        print(f"  {full} ({size} bytes)", flush=True)

print(f"[install_extensions] DONE — httpfs pre-installed in {EXT_DIR}", flush=True)
