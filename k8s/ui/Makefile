REGISTRY ?= your-registry
IMAGE_NAME ?= copilot-ui
TAG ?= latest

.PHONY: build push deploy dry-run

build:
	docker build -t $(REGISTRY)/$(IMAGE_NAME):$(TAG) -f ../../ui/Dockerfile ../../ui

push: build
	docker push $(REGISTRY)/$(IMAGE_NAME):$(TAG)

deploy:
	kubectl apply -k .

dry-run:
	kubectl apply -k . --dry-run=client
