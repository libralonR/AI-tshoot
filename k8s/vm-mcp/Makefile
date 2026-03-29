REGISTRY ?= your-registry
IMAGE_NAME ?= vm-mcp-server
VERSION ?= latest
NAMESPACE ?= observability
# Diretório onde você baixou o source do mcp-victoriametrics
VM_MCP_SOURCE ?= ./mcp-victoriametrics-source

.PHONY: build push deploy delete logs status clean

# Build: usa o Dockerfile oficial do source, builda direto de lá
build:
	@echo "Buildando imagem a partir de $(VM_MCP_SOURCE)..."
	docker build -t $(REGISTRY)/$(IMAGE_NAME):$(VERSION) $(VM_MCP_SOURCE)

push:
	docker push $(REGISTRY)/$(IMAGE_NAME):$(VERSION)

deploy:
	kubectl apply -k .

delete:
	kubectl delete -k .

logs:
	kubectl logs -n $(NAMESPACE) -l app=$(IMAGE_NAME) -f --tail=100

status:
	kubectl get pods -n $(NAMESPACE) -l app=$(IMAGE_NAME)
	kubectl get svc -n $(NAMESPACE) -l app=$(IMAGE_NAME)
