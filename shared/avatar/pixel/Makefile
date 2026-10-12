.PHONY: check test sync build

sync:
	uv sync --quiet

check: sync
	uv run ruff format --check .
	uv run ruff check .
	uv run pyright
	uv run pytest

test: sync
	uv run pytest

# Rebuild giraffe.png, giraffe.json and the previews in out/. Commit them with the build.py change.
build:
	python3 build.py
