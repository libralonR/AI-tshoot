#!/bin/bash
# Build and Deploy Grafana MCP Server to Kubernetes

set -euo pipefail

# Configuration
REGISTRY="${REGISTRY:-your-registry}"
IMAGE_NAME="${IMAGE_NAME:-grafana-mcp-server}"
VERSION="${VERSION:-v1.0.0}"
NAMESPACE="${NAMESPACE:-observability}"
SOURCE_FILE="../../mcp-servers/grafana_v2.py"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check docker
    if ! command -v docker &> /dev/null; then
        log_error "docker not found. Please install Docker."
        exit 1
    fi
    
    # Check kubectl
    if ! command -v kubectl &> /dev/null; then
        log_error "kubectl not found. Please install kubectl."
        exit 1
    fi
    
    # Check source file
    if [ ! -f "$SOURCE_FILE" ]; then
        log_error "Source file not found: $SOURCE_FILE"
        exit 1
    fi
    
    log_info "Prerequisites OK"
}

copy_source() {
    log_info "Copying source file..."
    cp "$SOURCE_FILE" ./grafana_v2.py
    log_info "Source copied"
}

build_image() {
    log_info "Building Docker image..."
    
    docker build \
        -t "${REGISTRY}/${IMAGE_NAME}:${VERSION}" \
        -t "${REGISTRY}/${IMAGE_NAME}:latest" \
        .
    
    log_info "Image built: ${REGISTRY}/${IMAGE_NAME}:${VERSION}"
}

push_image() {
    log_info "Pushing image to registry..."
    
    docker push "${REGISTRY}/${IMAGE_NAME}:${VERSION}"
    docker push "${REGISTRY}/${IMAGE_NAME}:latest"
    
    log_info "Image pushed"
}

create_namespace() {
    log_info "Creating namespace if not exists..."
    
    if kubectl get namespace "$NAMESPACE" &> /dev/null; then
        log_info "Namespace $NAMESPACE already exists"
    else
        kubectl create namespace "$NAMESPACE"
        log_info "Namespace $NAMESPACE created"
    fi
}

check_secrets() {
    log_info "Checking secrets..."
    
    if ! kubectl get secret grafana-mcp-secrets -n "$NAMESPACE" &> /dev/null; then
        log_warn "Secret grafana-mcp-secrets not found!"
        log_warn "Please create it with:"
        echo ""
        echo "kubectl create secret generic grafana-mcp-secrets \\"
        echo "  --from-literal=grafana-token='YOUR_TOKEN_HERE' \\"
        echo "  --namespace $NAMESPACE"
        echo ""
        read -p "Do you want to continue anyway? (y/N) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    else
        log_info "Secret exists"
    fi
}

deploy() {
    log_info "Deploying to Kubernetes..."
    
    # Update image in kustomization
    cd "$(dirname "$0")"
    
    # Apply with kustomize
    kubectl apply -k .
    
    log_info "Deployment applied"
}

wait_for_rollout() {
    log_info "Waiting for rollout to complete..."
    
    kubectl rollout status deployment/grafana-mcp-server -n "$NAMESPACE" --timeout=5m
    
    log_info "Rollout complete"
}

show_status() {
    log_info "Deployment status:"
    echo ""
    kubectl get pods -n "$NAMESPACE" -l app=grafana-mcp-server
    echo ""
    kubectl get svc -n "$NAMESPACE" grafana-mcp-server
    echo ""
    log_info "To view logs:"
    echo "kubectl logs -n $NAMESPACE -l app=grafana-mcp-server --tail=50 -f"
}

cleanup_temp() {
    log_info "Cleaning up temporary files..."
    rm -f ./grafana_v2.py
}

# Main
main() {
    log_info "Starting build and deploy process..."
    log_info "Registry: $REGISTRY"
    log_info "Image: $IMAGE_NAME"
    log_info "Version: $VERSION"
    log_info "Namespace: $NAMESPACE"
    echo ""
    
    check_prerequisites
    copy_source
    build_image
    
    # Ask before pushing
    read -p "Push image to registry? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        push_image
    else
        log_warn "Skipping image push"
    fi
    
    create_namespace
    check_secrets
    deploy
    wait_for_rollout
    show_status
    cleanup_temp
    
    log_info "Done!"
}

# Trap cleanup
trap cleanup_temp EXIT

# Run
main "$@"
