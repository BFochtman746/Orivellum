.PHONY: dev dev-mobile install

## Start API + web UI (one command)
dev:
	@bash scripts/dev.sh

## Start API + web UI + Expo mobile
dev-mobile:
	@bash scripts/dev.sh --mobile

## Install all dependencies (Python + Node)
install:
	uv sync
	pnpm install
