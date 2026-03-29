# Makefile para Grafana MCP Server Kubernetes Deployment

# Configuration
REGISTRY ?= your-registry
IMAGE_NAME ?= grafana-mcp-server
VERSION ?= v1.0.0
NAMESPACE ?= observability
SOURCE_FILE = ../../mcp-servers/grafana_v2.py

# Derived variables
IMAGE_TAG = $(REGISTRY)/$(IMAGE_NAME):$(VERSION)
IMAGE_LATEST = $(REGISTRY)/$(IMAGE_NAME):latest

.PHONY: help
help: ## Show this help
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

.PHONY: check
check: ## Check prerequisites
	@echo "Checking prerequisites..."
	@command -v docker >/dev/null 2>&1 || { echo "docker not found"; exit 1; }
	@command -v kubectl >/dev/null 2>&1 || { echo "kubectl not found"; exit 1; }
	@test -f $(SOURCE_FILE) || { echo "Source file not found: $(SOURCE_FILE)"; exit 1; }
	@echo "✓ Prerequisites OK"

.PHONY: copy-source
copy-source: ## Copy source file
	@echo "Copying source file..."
	@cp $(SOURCE_FILE) ./grafana_v2.py
	@echo "✓ Source copied"

.PHONY: build
build: check copy-source ## Build Docker image
	@echo "Building image $(IMAGE_TAG)..."
	docker build -t $(IMAGE_TAG) -t $(IMAGE_LATEST) .
	@echo "✓ Image built"

.PHONY: push
push: ## Push image to registry
	@echo "Pushing image..."
	docker push $(IMAGE_TAG)
	docker push $(IMAGE_LATEST)
	@echo "✓ Image pushed"

.PHONY: create-namespace
create-namespace: ## Create namespace
	@echo "Creating namespace $(NAMESPACE)..."
	@kubectl create namespace $(NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ Namespace ready"

.PHONY: create-secret
create-secret: ## Create secret (interactive)
	@echo "Creating secret..."
	@read -p "Enter Grafana token: " token; \
	kubectl create secret generic grafana-mcp-secrets \
		--from-literal=grafana-token="$$token" \
		--namespace $(NAMESPACE) \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "✓ Secret created"

.PHONY: deploy
deploy: create-namespace ## Deploy to Kubernetes
	@echo "Deploying to $(NAMESPACE)..."
	kubectl apply -k .
	@echo "✓ Deployed"

.PHONY: rollout-status
rollout-status: ## Wait for rollout to complete
	@echo "Waiting for rollout..."
	kubectl rollout status deployment/grafana-mcp-server -n $(NAMESPACE) --timeout=5m

.PHONY: status
status: ## Show deployment status
	@echo "Pods:"
	@kubectl get pods -n $(NAMESPACE) -l app=grafana-mcp-server
	@echo ""
	@echo "Service:"
	@kubectl get svc -n $(NAMESPACE) grafana-mcp-server
	@echo ""
	@echo "HPA:"
	@kubectl get hpa -n $(NAMESPACE) grafana-mcp-server 2>/dev/null || echo "HPA not found"

.PHONY: logs
logs: ## Show logs
	kubectl logs -n $(NAMESPACE) -l app=grafana-mcp-server --tail=100 -f

.PHONY: describe
describe: ## Describe deployment
	kubectl describe deployment grafana-mcp-server -n $(NAMESPACE)

.PHONY: port-forward
port-forward: ## Port-forward to local (8080)
	kubectl port-forward -n $(NAMESPACE) svc/grafana-mcp-server 8080:8080

.PHONY: restart
restart: ## Restart deployment
	kubectl rollout restart deployment/grafana-mcp-server -n $(NAMESPACE)

.PHONY: scale
scale: ## Scale deployment (usage: make scale REPLICAS=3)
	kubectl scale deployment/grafana-mcp-server -n $(NAMESPACE) --replicas=$(REPLICAS)

.PHONY: delete
delete: ## Delete all resources
	kubectl delete -k .

.PHONY: clean
clean: ## Clean temporary files
	rm -f grafana_v2.py

.PHONY: all
all: build push deploy rollout-status status ## Build, push, and deploy

.PHONY: dev
dev: build deploy rollout-status logs ## Quick dev cycle (build + deploy + logs)

# Troubleshooting targets
.PHONY: debug-pod
debug-pod: ## Get shell in a pod
	@POD=$$(kubectl get pods -n $(NAMESPACE) -l app=grafana-mcp-server -o jsonpath='{.items[0].metadata.name}'); \
	kubectl exec -it -n $(NAMESPACE) $$POD -- /bin/sh

.PHONY: test-grafana-connection
test-grafana-connection: ## Test Grafana connection from pod
	@POD=$$(kubectl get pods -n $(NAMESPACE) -l app=grafana-mcp-server -o jsonpath='{.items[0].metadata.name}'); \
	kubectl exec -n $(NAMESPACE) $$POD -- python -c "import httpx; print(httpx.get('$$GRAFANA_URL/api/health').text)"

.PHONY: events
events: ## Show recent events
	kubectl get events -n $(NAMESPACE) --sort-by='.lastTimestamp' | grep grafana-mcp

.PHONY: top
top: ## Show resource usage
	kubectl top pods -n $(NAMESPACE) -l app=grafana-mcp-server
