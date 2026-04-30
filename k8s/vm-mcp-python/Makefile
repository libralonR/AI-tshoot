REGISTRY ?= your-registry
IMAGE_NAME ?= vm-mcp-python
TAG ?= latest

.PHONY: build push deploy dry-run

build:
	docker build -t $(REGISTRY)/$(IMAGE_NAME):$(TAG) -f Dockerfile ../..

push: build
	docker push $(REGISTRY)/$(IMAGE_NAME):$(TAG)

deploy:
	kubectl apply -k .

dry-run:
	kubectl apply -k . --dry-run=client
