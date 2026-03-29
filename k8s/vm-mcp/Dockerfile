# Build stage — compila o MCP server da VictoriaMetrics a partir do source local
# Pré-requisito: baixar o source de https://github.com/VictoriaMetrics/mcp-victoriametrics
# e colocar os arquivos neste diretório (go.mod, go.sum, cmd/, web/, etc.)
FROM golang:1.26-alpine AS builder
WORKDIR /app

RUN apk add --no-cache git ca-certificates nodejs npm

COPY go.mod go.sum ./
COPY . .

# Build UI
RUN cd web && npm install && npm run build

# Build binary
RUN go mod download
RUN go build -o mcp-victoriametrics ./cmd/mcp-victoriametrics

# Runtime stage
FROM alpine:latest
WORKDIR /app

RUN apk add --no-cache ca-certificates
RUN adduser -D -u 1000 mcp

COPY --from=builder /app/mcp-victoriametrics /usr/local/bin/mcp-victoriametrics

USER mcp

ENTRYPOINT ["mcp-victoriametrics"]
