.PHONY: check test sync fixtures preview

sync:
	uv sync --quiet

check: sync
	uv run ruff format --check .
	uv run ruff check .
	uv run pyright
	uv run pytest

test: sync
	uv run pytest

# Re-export the fixtures from zoo.js (needs node). Only when zoo.js changed on purpose; commit the result.
fixtures:
	node tests/fixtures/export.js

# A preview of every mood for one look: make preview ARGS="--animal frog --personality bold"
preview: sync
	uv run python -m zoo preview $(ARGS)
