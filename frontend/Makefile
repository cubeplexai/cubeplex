.PHONY: help install build-core build lint type-check format format-check test-unit test-e2e test-e2e-ci install-browsers check-i18n check-ci

help:
	@echo "Available commands:"
	@echo "  make install           - Install frontend dependencies"
	@echo "  make build-core        - Build @cubeplex/core"
	@echo "  make build             - Build frontend packages"
	@echo "  make lint              - Run frontend lint"
	@echo "  make type-check        - Run frontend type checks"
	@echo "  make format            - Format frontend files"
	@echo "  make format-check      - Check frontend formatting"
	@echo "  make test-unit         - Run frontend unit tests"
	@echo "  make test-e2e          - Run Playwright E2E tests"
	@echo "  make test-e2e-ci       - Run Playwright E2E tests with CI output"
	@echo "  make install-browsers  - Install Playwright Chromium browser"
	@echo "  make check-i18n        - Check i18n locale parity + dynamic t() calls"
	@echo "  make check-ci          - Run frontend CI checks"

install:
	pnpm install --frozen-lockfile

build-core:
	pnpm --filter @cubeplex/core build

build:
	pnpm build

lint:
	pnpm -r lint

type-check:
	pnpm -r type-check

format:
	pnpm format

format-check:
	pnpm format:check

test-unit:
	pnpm -r test

test-e2e:
	pnpm test:e2e

test-e2e-ci:
	PLAYWRIGHT_FORCE_TTY=1 pnpm test:e2e

install-browsers:
	pnpm exec playwright install --with-deps chromium

check-i18n:
	node scripts/check-i18n-keys.mjs

check-ci: build-core lint format-check check-i18n type-check test-unit build
