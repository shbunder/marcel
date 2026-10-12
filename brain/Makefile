.PHONY: check fmt test sync render

sync:
	uv sync --quiet

check: sync
	uv run ruff format --check .
	uv run ruff check .
	uv run pyright
	uv run pytest --cov --cov-report=term-missing

fmt: sync
	uv run ruff format .
	uv run ruff check --fix .

test: sync
	uv run pytest
