.PHONY: dev install

## Start API + web UI (one command)
dev:
	@bash scripts/dev.sh

## Install all dependencies (Python + Node)
install:
	uv sync
	pnpm install
