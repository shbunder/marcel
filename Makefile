# ==== REFACTORED BY SBU ===
SHELL := /bin/bash
.DEFAULT_GOAL := help

# INFO / WARNING prefix for echo's
INFO := \033[1;32m[   INFO]\033[0m
WARNING := \033[0;93m[WARNING]\033[0m

include .env
-include .env.local

# Export all variables to subprocesses
.EXPORT_ALL_VARIABLES:

# ENVIRONMENT SETUP
.PHONY: .uv
.uv: ## Check that uv is installed
	@uv --version || echo -e "$(WARNING) Please install uv: https://docs.astral.sh/uv/getting-started/installation/"

.PHONY: env-install
env-install: .uv ## Install the package, dependencies, and pre-commit for local development
	echo -e "$(INFO) Installing packages and depencies..."
	uv sync --frozen --all-extras --all-packages --group dev --group lint --group docs

.PHONY: env-sync
env-sync: .uv ## Update local packages and uv.lock
	echo -e "$(INFO) Updating packages and uv.lock..."
	uv sync --all-extras --all-packages --group lint --group docs

# DOCUMENTATION
# `--no-strict` so you can build the docs without insiders packages
.PHONY: docs-build
docs-build: ## Build the documentation
	echo -e "$(INFO) Building documentation..."
	uv run mkdocs build --no-strict

# `--no-strict` so you can build the docs without insiders packages
.PHONY: docs-serve
docs-serve: ## Build and serve the documentation
	echo -e "$(INFO) Serving documentation..."
	uv run mkdocs serve --no-strict

# TESTING
.PHONY: test
test: ## Run all tests (Python + Rust)
	echo -e "$(INFO) Running Python tests..."
	uv run pytest tests/ -x -v
	echo -e "$(INFO) Running Rust CLI tests..."
	cd src/marcel_cli && cargo test

.PHONY: test-core
test-core: ## Run core package tests
	echo -e "$(INFO) Running core-tests..."
	uv run pytest tests/core/ -x -v

.PHONY: test-cov
test-cov: ## Run tests with coverage report
	echo -e "$(INFO) Running all tests with coverage..."
	uv run pytest tests/ --cov=execution_garden_core --cov-report=term-missing

.PHONY: install-cli
install-cli: ## Install the Marcel CLI binary (Rust) to ~/.cargo/bin
	echo -e "$(INFO) Building and installing Marcel CLI..."
	cd src/marcel_cli && cargo install --path .

.PHONY: cli
cli: cli-build ## Start the Marcel CLI (Rust TUI)
	./src/marcel_cli/target/release/marcel

.PHONY: cli-build
cli-build: ## Build the Marcel CLI (Rust, release mode)
	echo -e "$(INFO) Building Marcel CLI..."
	cd src/marcel_cli && cargo build --release

.PHONY: cli-dev
cli-dev: ## Build and run the Marcel CLI (Rust, debug mode)
	cd src/marcel_cli && cargo run

# WEB FRONTEND
.PHONY: web-install
web-install: ## Install web frontend dependencies
	echo -e "$(INFO) Installing web dependencies..."
	cd src/web && npm ci

.PHONY: web-build
web-build: ## Build web frontend for production
	echo -e "$(INFO) Building web frontend..."
	cd src/web && npm run build

.PHONY: web-dev
web-dev: ## Start web frontend dev server (Vite on :5173)
	cd src/web && npm run dev

# Dev server port — defaults to 7421 to avoid conflicting with Docker prod (7420)
MARCEL_DEV_PORT ?= 7421

.PHONY: install-skills
install-skills: ## Symlink Marcel integration skills into .claude/skills/
	@echo -e "$(INFO) Installing integration skills (symlink)..."
	@uv run python -m marcel_core.skills.install_skills

.PHONY: serve
serve: install-skills ## Start marcel-core development server (uvicorn with reload)
	echo -e "$(INFO) Starting marcel-core (dev) on http://0.0.0.0:$(MARCEL_DEV_PORT) ..."
	MARCEL_PORT=$(MARCEL_DEV_PORT) uv run uvicorn marcel_core.main:app --host 0.0.0.0 --port $(MARCEL_DEV_PORT) --reload

.PHONY: test-v2
test-v2: ## Test v2 endpoint with a message (usage: make test-v2 MSG="your message")
	@if [ -z "$(MSG)" ]; then \
		echo -e "$(WARNING) Usage: make test-v2 MSG=\"your message here\""; \
		echo "Examples:"; \
		echo "  make test-v2 MSG=\"Hello Marcel!\""; \
		echo "  make test-v2 MSG=\"List files in current directory\""; \
		echo "  make test-v2 MSG=\"Read README.md\""; \
		exit 1; \
	fi
	@./test_v2.sh "$(MSG)"

# Deployment
.PHONY: setup
setup: ## Full setup: systemd units + Docker build + start (one command to rule them all)
	@./deploy/setup.sh

.PHONY: teardown
teardown: ## Stop Marcel and remove systemd units
	@./deploy/teardown.sh

# Docker targets
.PHONY: docker-build
docker-build: ## Build the Marcel Docker image
	echo -e "$(INFO) Building Marcel Docker image..."
	docker compose build

.PHONY: docker-up
docker-up: ## Start Marcel in Docker (production)
	echo -e "$(INFO) Starting Marcel (Docker) on port 7420..."
	docker compose up -d

.PHONY: docker-down
docker-down: ## Stop Marcel Docker container
	docker compose down

.PHONY: docker-logs
docker-logs: ## Tail Marcel Docker logs
	docker compose logs -f marcel

.PHONY: docker-restart
docker-restart: ## Rebuild and restart Marcel Docker container
	echo -e "$(INFO) Redeploying Marcel..."
	./redeploy.sh

.PHONY: check
check: format lint typecheck test-cov ## Run format, lint, typecheck, and tests

# CODE QUALITY
.PHONY: format
format: ## Format the code (Python + Rust)
	echo -e "$(INFO) Formatting Python code..."
	uv run ruff format
	uv run ruff check --fix --fix-only
	echo -e "$(INFO) Formatting Rust code..."
	cd src/marcel_cli && cargo fmt

.PHONY: lint
lint: ## Lint the code (Python + Rust)
	echo -e "$(INFO) Linting Python code..."
	uv run ruff format --check
	uv run ruff check
	echo -e "$(INFO) Linting Rust code..."
	cd src/marcel_cli && cargo clippy -- -D warnings

.PHONY: typecheck-pyright
typecheck-pyright:
	echo -e "$(INFO) Typechecking code with pyright..."
	@# To typecheck for a specific version of python, run 'make install-all-python' then set environment variable PYRIGHT_PYTHON=3.10 or similar
	@# PYRIGHT_PYTHON_IGNORE_WARNINGS avoids the overhead of making a request to github on every invocation
	PYRIGHT_PYTHON_IGNORE_WARNINGS=1 uv run pyright $(if $(PYRIGHT_PYTHON),--pythonversion $(PYRIGHT_PYTHON))

.PHONY: typecheck
typecheck: typecheck-pyright ## Run static type checking

.PHONY: help
help: ## Show this help
	@echo "Usage: make [target]"
	@echo ""
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		sed 's/^.*Makefile://g' | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "For detailed commands, check the Makefile or run: make -n <target>"
