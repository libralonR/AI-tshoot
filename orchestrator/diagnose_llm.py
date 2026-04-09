#!/usr/bin/env python3
"""
Script de diagnóstico para problemas de conectividade com LLM Gateway.
Executar dentro do pod do orchestrator:
  kubectl exec -it deployment/orchestrator -n copilot -- python3 diagnose_llm.py
"""

import asyncio
import os
import sys
import time
from urllib.parse import urlparse

import httpx


def print_header(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def print_result(test_name, success, details=""):
    status = "✓ PASS" if success else "✗ FAIL"
    print(f"{status:8} | {test_name}")
    if details:
        print(f"         | {details}")


async def test_dns_resolution(hostname):
    """Testa resolução DNS."""
    print_header("1. DNS Resolution")
    try:
        import socket
        start = time.time()
        ip = socket.gethostbyname(hostname)
        elapsed = time.time() - start
        print_result("DNS Resolution", True, f"{hostname} → {ip} ({elapsed:.3f}s)")
        return True, ip
    except Exception as e:
        print_result("DNS Resolution", False, f"{hostname} → {str(e)}")
        return False, None


async def test_tcp_connection(hostname, port):
    """Testa conexão TCP."""
    print_header("2. TCP Connection")
    try:
        start = time.time()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(hostname, port),
            timeout=10.0
        )
        elapsed = time.time() - start
        writer.close()
        await writer.wait_closed()
        print_result("TCP Connection", True, f"{hostname}:{port} ({elapsed:.3f}s)")
        return True
    except asyncio.TimeoutError:
        print_result("TCP Connection", False, f"Timeout após 10s")
        return False
    except Exception as e:
        print_result("TCP Connection", False, f"{str(e)}")
        return False


async def test_https_connection(url):
    """Testa conexão HTTPS."""
    print_header("3. HTTPS Connection")
    try:
        async with httpx.AsyncClient(
            verify=False,
            timeout=httpx.Timeout(10.0, connect=5.0)
        ) as client:
            start = time.time()
            response = await client.get(url)
            elapsed = time.time() - start
            print_result(
                "HTTPS GET",
                response.status_code < 500,
                f"Status {response.status_code} ({elapsed:.3f}s)"
            )
            return response.status_code < 500
    except httpx.ConnectTimeout:
        print_result("HTTPS GET", False, "Connection timeout")
        return False
    except httpx.ConnectError as e:
        print_result("HTTPS GET", False, f"Connection error: {str(e)[:100]}")
        return False
    except Exception as e:
        print_result("HTTPS GET", False, f"{type(e).__name__}: {str(e)[:100]}")
        return False


async def test_openai_client():
    """Testa cliente OpenAI."""
    print_header("4. OpenAI Client")
    
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    model = os.getenv("OPENAI_MODEL", "gpt-4o")
    timeout = float(os.getenv("OPENAI_TIMEOUT", "60"))
    
    if not api_key:
        print_result("API Key", False, "OPENAI_API_KEY not set")
        return False
    
    print_result("API Key", True, f"Set (length: {len(api_key)})")
    print_result("Base URL", bool(base_url), base_url or "Not set (using default)")
    print_result("Model", True, model)
    print_result("Timeout", True, f"{timeout}s")
    
    if not base_url:
        print("\n⚠️  OPENAI_BASE_URL not set, skipping API test")
        return False
    
    try:
        from openai import AsyncOpenAI
        
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=httpx.AsyncClient(
                verify=False,
                timeout=httpx.Timeout(timeout=10.0, connect=5.0)
            ),
            max_retries=0
        )
        
        start = time.time()
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "test"}],
            max_tokens=5,
            temperature=0
        )
        elapsed = time.time() - start
        
        print_result(
            "API Call",
            True,
            f"Success ({elapsed:.3f}s, {response.usage.total_tokens} tokens)"
        )
        return True
        
    except Exception as e:
        print_result("API Call", False, f"{type(e).__name__}: {str(e)[:200]}")
        return False


async def test_mcp_servers():
    """Testa conectividade com MCP servers."""
    print_header("5. MCP Servers")
    
    servers = {
        "Grafana MCP": "http://grafana-mcp-server.observability.svc.cluster.local:8080/health",
        "Incidents PG MCP": "http://incidents-pg-mcp-server.observability.svc.cluster.local:8080/health",
    }
    
    results = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, url in servers.items():
            try:
                start = time.time()
                response = await client.get(url)
                elapsed = time.time() - start
                success = response.status_code == 200
                print_result(
                    name,
                    success,
                    f"Status {response.status_code} ({elapsed:.3f}s)"
                )
                results.append(success)
            except Exception as e:
                print_result(name, False, f"{type(e).__name__}: {str(e)[:100]}")
                results.append(False)
    
    return all(results)


async def main():
    print("\n" + "="*60)
    print("  Observability Copilot - LLM Gateway Diagnostics")
    print("="*60)
    
    # Ler configuração
    base_url = os.getenv("OPENAI_BASE_URL")
    if not base_url:
        print("\n⚠️  OPENAI_BASE_URL not set!")
        print("   Set it with: export OPENAI_BASE_URL=https://...")
        return 1
    
    parsed = urlparse(base_url)
    hostname = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    
    print(f"\nTarget: {base_url}")
    print(f"Hostname: {hostname}")
    print(f"Port: {port}")
    
    # Executar testes
    results = []
    
    # 1. DNS
    dns_ok, ip = await test_dns_resolution(hostname)
    results.append(dns_ok)
    
    # 2. TCP
    if dns_ok:
        tcp_ok = await test_tcp_connection(hostname, port)
        results.append(tcp_ok)
    else:
        print_header("2. TCP Connection")
        print_result("TCP Connection", False, "Skipped (DNS failed)")
        results.append(False)
    
    # 3. HTTPS
    https_ok = await test_https_connection(base_url)
    results.append(https_ok)
    
    # 4. OpenAI Client
    openai_ok = await test_openai_client()
    results.append(openai_ok)
    
    # 5. MCP Servers
    mcp_ok = await test_mcp_servers()
    results.append(mcp_ok)
    
    # Resumo
    print_header("Summary")
    passed = sum(results)
    total = len(results)
    print(f"Tests passed: {passed}/{total}")
    
    if passed == total:
        print("\n✓ All tests passed! LLM Gateway is reachable.")
        return 0
    else:
        print("\n✗ Some tests failed. Check logs above for details.")
        print("\nTroubleshooting:")
        if not dns_ok:
            print("  • DNS resolution failed - check CoreDNS configuration")
        if dns_ok and not results[1]:  # TCP failed
            print("  • TCP connection failed - check firewall/NetworkPolicy")
        if not https_ok:
            print("  • HTTPS connection failed - check gateway availability")
        if not openai_ok:
            print("  • OpenAI client failed - check API key and model")
        if not mcp_ok:
            print("  • MCP servers unreachable - check deployments")
        
        print("\nSee docs/LLM_GATEWAY_TROUBLESHOOTING.md for more details.")
        return 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n\n✗ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
