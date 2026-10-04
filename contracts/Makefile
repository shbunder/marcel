.PHONY: check test sync

sync:
	uv sync --quiet

check: sync
	uv run ruff format --check .
	uv run ruff check .
	uv run pyright
	uv run pytest

test: sync
	uv run pytest
