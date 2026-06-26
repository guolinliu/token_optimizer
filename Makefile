# claude-gists developer/build tasks.
# Everything runs through `uv` so no manual venv management is needed.

.DEFAULT_GOAL := help
.PHONY: help install test run lint dist binary publish-test publish clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Create the dev environment
	uv sync --extra dev --extra build

test: ## Run the test suite
	uv run --extra dev pytest -q

run: ## Launch the TUI from source
	uv run claude-gists

dist: ## Build the wheel + sdist into dist/
	uv build

binary: ## Build a standalone executable into dist/ (PyInstaller)
	uv run --extra build pyinstaller --clean --noconfirm claude-gists.spec
	@echo "Built: dist/claude-gists"

publish-test: dist ## Upload to TestPyPI (set UV_PUBLISH_TOKEN)
	uv publish --publish-url https://test.pypi.org/legacy/

publish: dist ## Upload to PyPI (set UV_PUBLISH_TOKEN)
	uv publish

clean: ## Remove build artifacts
	rm -rf dist build *.egg-info .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
