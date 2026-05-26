FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy hexagonal application code
COPY domain/ ./domain/
COPY application/ ./application/
COPY infrastructure/ ./infrastructure/
COPY api/ ./api/

# Non-root user for security
RUN useradd -m -u 1000 orchestrator && chown -R orchestrator:orchestrator /app
USER orchestrator

# PYTHONPATH ensures the four top-level packages resolve as modules
ENV PYTHONPATH=/app

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import httpx; httpx.get('http://localhost:8080/health')"

# Expose port
EXPOSE 8080

# Run orchestrator (FastAPI app entrypoint)
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
