REGISTRY ?= your-registry
IMAGE_NAME ?= incidents-pg-mcp-server
VERSION ?= latest
NAMESPACE ?= observability

.PHONY: build push deploy delete logs status

build:
	cp ../../mcp-servers/incidents_pg.py ./incidents_pg.py
	cp ../../mcp-servers/requirements.txt ./requirements.txt
	docker build -t $(REGISTRY)/$(IMAGE_NAME):$(VERSION) .
	rm -f incidents_pg.py requirements.txt

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
