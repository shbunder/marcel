.PHONY: check test sync mutants

sync:
	uv sync --quiet

check: sync
	uv run ruff format --check .
	uv run ruff check .
	uv run pyright
	uv run pytest

test: sync
	uv run pytest

# Not part of the gate: deletes each if/then rule in turn and checks a test catches it.
mutants: sync
	uv run python scripts/mutate_conditionals.py
