.PHONY: help env build up down logs ps download download-all models bench stop-backends dev

help:
	@echo "make env            create .env from .env.example"
	@echo "make build          build all images (frontend, controller, backend, tools)"
	@echo "make up / down      start / stop the frontend and controller"
	@echo "make download M=<registry key>   fetch weights into the models volume"
	@echo "make download-all   fetch every model in config/models.yaml"
	@echo "make models         list registry models and whether weights are present"
	@echo "make bench          run benchmarks/suite.yaml across models"
	@echo "make stop-backends  remove every running TTS backend container"
	@echo "make dev            run the frontend with hot reload on :5173 (needs the controller up)"

env:
	@test -f .env || (cp .env.example .env && echo "created .env; edit the secrets")

build:
	docker compose --profile tools build

up:
	docker compose up -d

down: stop-backends
	docker compose down

logs:
	docker compose logs -f frontend controller

ps:
	docker compose ps
	docker ps --filter label=ttsbench.managed=true

download:
	docker compose run --rm downloader --key $(M)

download-all:
	docker compose run --rm downloader --all

models:
	docker compose run --rm downloader --list

bench:
	docker compose run --rm bench /benchmarks/suite.yaml

stop-backends:
	-curl -fsS -X POST http://localhost:$${CONTROLLER_PORT:-8090}/backends/stop-all; echo

dev:
	docker run --rm -it -p 5173:5173 -v $(CURDIR)/frontend:/app -w /app --network host \
		node:22-alpine sh -c "npm ci && npx vite --host 0.0.0.0"
