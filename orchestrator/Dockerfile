FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY orchestrator.py config.py models.py mcp_client.py guardrails.py correlation.py hypothesis.py llm_client.py ./
COPY agents/ ./agents/

# Copy steering files (context persistente)
COPY steering/ ./steering/

# Copy prompts (system prompts)
COPY prompts/ ./prompts/

# Copy specs (design specs)
COPY specs/ ./specs/

COPY test_orchestrator.py .

# Non-root user for security
RUN useradd -m -u 1000 orchestrator && chown -R orchestrator:orchestrator /app
USER orchestrator

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8080/health')"

# Expose port
EXPOSE 8080

# Run orchestrator
CMD ["python", "orchestrator.py"]
