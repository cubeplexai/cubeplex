.PHONY: help install skills-restore backend-install frontend-install backend-check-ci frontend-check-ci check-ci backend-migrate backend-test-e2e backend-test-e2e-ci backend-test-contracts backend-start backend-cleanup-sandboxes frontend-build-core frontend-install-browsers frontend-test-e2e frontend-test-e2e-ci test-ui-unit test-ui-e2e test-ui test-all clean

help:
	@echo "Available commands:"
	@echo "  make install                    - Install backend and frontend dependencies"
	@echo "  make skills-restore             - Restore vendored skills (skills-lock.json) + wire symlinks"
	@echo "  make backend-install            - Install backend development dependencies"
	@echo "  make frontend-install           - Install frontend dependencies"
	@echo "  make backend-check-ci           - Run backend CI checks"
	@echo "  make frontend-check-ci          - Run frontend CI checks"
	@echo "  make check-ci                   - Run backend and frontend CI checks"
	@echo "  make backend-migrate            - Run backend Alembic migrations"
	@echo "  make backend-test-e2e           - Run backend E2E tests"
	@echo "  make backend-test-e2e-ci        - Run backend E2E tests, writing coverage-e2e.xml for Codecov"
	@echo "  make backend-test-contracts     - Run plugin contract tests (EE compat)"
	@echo "  make backend-start              - Start backend server"
	@echo "  make backend-cleanup-sandboxes  - Cleanup leftover test sandboxes"
	@echo "  make frontend-build-core        - Build @cubeplex/core"
	@echo "  make frontend-install-browsers  - Install Playwright Chromium browser"
	@echo "  make frontend-test-e2e          - Run frontend Playwright tests"
	@echo "  make frontend-test-e2e-ci       - Run frontend Playwright tests with CI output"
	@echo "  make test-ui-unit               - Run frontend unit tests"
	@echo "  make test-ui-e2e                - Run frontend Playwright tests"
	@echo "  make test-ui                    - Run all frontend tests"
	@echo "  make test-all                   - Run all backend + frontend tests"
	@echo "  make clean                      - Clean backend cache and build files"

install: backend-install frontend-install

# Restore vendored skills (content from skills-lock.json) and wire the
# .claude/skills symlinks. `experimental_install` only restores .agents/skills
# content — in a single-agent repo it copies without symlinking — so we create
# any missing symlinks ourselves. Native skills are committed and untouched.
skills-restore:
	npx skills experimental_install
	@mkdir -p .claude/skills
	@for d in .agents/skills/*/; do n=$$(basename "$$d"); [ -e ".claude/skills/$$n" ] || ln -s "../../.agents/skills/$$n" ".claude/skills/$$n"; done
	@echo "Skills restored: .agents/skills content + .claude/skills symlinks."

backend-install:
	$(MAKE) -C backend dev-install

frontend-install:
	$(MAKE) -C frontend install

backend-check-ci:
	$(MAKE) -C backend check-ci

frontend-check-ci:
	$(MAKE) -C frontend check-ci

check-ci: backend-check-ci frontend-check-ci

backend-migrate:
	$(MAKE) -C backend migrate

backend-test-e2e:
	$(MAKE) -C backend test-e2e

backend-test-e2e-ci:
	$(MAKE) -C backend test-e2e-ci

backend-test-contracts:
	$(MAKE) -C backend test-contracts

backend-start:
	$(MAKE) -C backend start

backend-cleanup-sandboxes:
	$(MAKE) -C backend cleanup-sandboxes

frontend-build-core:
	$(MAKE) -C frontend build-core

frontend-install-browsers:
	$(MAKE) -C frontend install-browsers

frontend-test-e2e:
	$(MAKE) -C frontend test-e2e

frontend-test-e2e-ci:
	$(MAKE) -C frontend test-e2e-ci

test-ui-unit:
	$(MAKE) -C frontend test-unit

test-ui-e2e: frontend-test-e2e

test-ui: test-ui-unit test-ui-e2e

test-all:
	$(MAKE) -C backend test
	$(MAKE) -C frontend test-unit
	$(MAKE) -C frontend test-e2e

clean:
	$(MAKE) -C backend clean
