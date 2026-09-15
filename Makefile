.PHONY: help setup install validate check-bc generate clean test test-node test-python clone-sdk-ref refresh-sdk-ref reference

# Reference clones live in _references/, created by `make clone-sdk-ref`.
# Point SDK_PYTHON / SDK_JAVA at your own clone to override.
SDK_PYTHON ?=
SDK_JAVA ?=

# Colors for output
GREEN := \033[0;32m
RED := \033[0;31m
BLUE := \033[0;34m
NC := \033[0m # No Color

help: ## Show this help message
	@echo "$(BLUE)SDK Automation POC - Available Commands$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "$(GREEN)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "Requirements:"
	@echo "  Python 3.9+             # For openapi-spec-validator & breaking-change-detector"
	@echo "  Node.js 18+             # For OpenAPI Generator CLI & tests (npm)"
	@echo "  Java 11+ JRE            # For SDK generation (OpenAPI Generator CLI runtime)"
	@echo ""
	@echo "Examples:"
	@echo "  make setup              # First-time setup (install all deps)"
	@echo "  make validate           # Validate OpenAPI spec"
	@echo "  make check-bc           # Detect breaking changes"
	@echo "  make generate           # Generate Node SDK (requires Java 11+ JRE)"
	@echo "  make generate-all       # Generate all SDKs (requires Java 11+ JRE)"
	@echo "  make clean              # Clean generated files"

# ============================================
# Setup & Installation
# ============================================

setup: install clone-sdk-ref ## Setup project (install deps + clone Python SDK ref)
	@echo "$(GREEN)✅ Setup complete!$(NC)"
	@echo ""
	@echo "Next steps:"
	@echo "  make validate           # Validate spec"
	@echo "  make check-bc           # Check breaking changes"
	@echo "  make generate           # Generate Node SDK"
	@echo "  make generate-all       # Generate all SDKs"

install: install-python install-node ## Install Python + Node.js dependencies

install-python: ## Install Python dependencies (openapi-spec-validator)
	@echo "$(BLUE)Installing Python dependencies...$(NC)"
	@echo "$(BLUE)Note: Ensure Python venv is activated (venv, virtualenvwrapper, pyenv, etc.)$(NC)"
	pip install -q -r requirements.txt
	@echo "$(GREEN)✅ Python dependencies installed$(NC)"

install-node: ## Install Node.js dependencies (npm)
	@echo "$(BLUE)Installing Node.js dependencies...$(NC)"
	npm install
	@echo "$(GREEN)✅ Node.js dependencies installed$(NC)"

clone-sdk-ref: ## Clone the SDK references into _references/ (override with SDK_PYTHON / SDK_JAVA)
	@mkdir -p _references
	@$(MAKE) --no-print-directory reference REPO=sdk-python MARKER=starkbank OVERRIDE="$(SDK_PYTHON)"
	@$(MAKE) --no-print-directory reference REPO=sdk-java MARKER=src/main/java/com/starkbank OVERRIDE="$(SDK_JAVA)"
	@$(MAKE) --no-print-directory reference REPO=sdk-node MARKER=sdk OVERRIDE="$(SDK_NODE)"

refresh-sdk-ref: ## Atualiza os clones em _references/ (override por symlink e preservado)
	@for repo in sdk-python sdk-java sdk-node; do \
		if [ -L "_references/$$repo" ]; then \
			echo "$(BLUE)↷ $$repo: override, nao atualizado$(NC)"; \
		elif [ -d "_references/$$repo/.git" ]; then \
			git -C "_references/$$repo" fetch --quiet --depth 1 origin HEAD && \
			git -C "_references/$$repo" checkout --quiet --detach FETCH_HEAD && \
			echo "$(GREEN)✅ $$repo: $$(git -C _references/$$repo rev-parse --short HEAD)$(NC)"; \
		else \
			echo "$(RED)❌ $$repo: sem clone em _references, rode make clone-sdk-ref$(NC)"; exit 1; \
		fi; \
	done

reference: ## Internal: resolve one reference clone
	@if [ -n "$(OVERRIDE)" ]; then \
		if [ ! -d "$(OVERRIDE)/$(MARKER)" ]; then \
			echo "$(RED)❌ $(REPO): $(OVERRIDE) nao contem $(MARKER)$(NC)"; exit 1; \
		fi; \
		ln -sfn "$(OVERRIDE)" _references/$(REPO); \
		echo "$(GREEN)✅ $(REPO): $(OVERRIDE) (override)$(NC)"; \
	elif [ -d "_references/$(REPO)/$(MARKER)" ]; then \
		echo "$(GREEN)✅ $(REPO): _references/$(REPO)$(NC)"; \
	else \
		git clone -q --depth 1 https://github.com/starkbank/$(REPO).git _references/$(REPO); \
		echo "$(GREEN)✅ $(REPO): clonado em _references/$(REPO)$(NC)"; \
	fi

# ============================================
# Tests
# ============================================

test: test-node test-python ## Run all unit tests (Jest + pytest)
	@echo "$(GREEN)✅ All tests passed$(NC)"

test-node: ## Run Node unit tests (Jest)
	@echo "$(BLUE)Running Node tests (Jest)...$(NC)"
	npm test

test-python: ## Run Python tool tests (pytest)
	@echo "$(BLUE)Running Python tests (pytest)...$(NC)"
	python3 -m pytest tests/ -q

test-watch: ## Run tests in watch mode (re-run on file change)
	@echo "$(BLUE)Running tests in watch mode...$(NC)"
	npm run test:watch

# ============================================
# Validation & Checks
# ============================================

validate: ## Validate OpenAPI spec (Python)
	@echo "$(BLUE)Validating OpenAPI spec...$(NC)"
	openapi-spec-validator apis/spec-v2.openapi.yaml
	@echo "$(GREEN)✅ Spec validation passed$(NC)"

check-bc: ## Detect breaking changes (Python)
	@echo "$(BLUE)Checking for breaking changes...$(NC)"
	python3 tools/breaking-change-detector.py
	@echo "$(GREEN)✅ Breaking change check passed$(NC)"

check: validate check-bc test ## Run all checks (validate + breaking-changes + tests)
	@echo ""
	@echo "$(GREEN)✅ All checks passed!$(NC)"

# ============================================
# SDK Generation
# ============================================

check-generator-cli: ## Check OpenAPI Generator CLI is installed and meets minimum version (v2.41.0+)
	@echo "$(BLUE)Checking OpenAPI Generator CLI version...$(NC)"
	@version_output=$$(npx openapi-generator-cli version 2>&1); \
	version_string=$$(echo "$$version_output" | grep -oE "^[0-9]+\.[0-9]+\.[0-9]+"); \
	if [ -z "$$version_string" ]; then \
		echo "$(RED)❌ OpenAPI Generator CLI v2.41.0+ not found$(NC)"; \
		exit 1; \
	fi; \
	major=$$(echo "$$version_string" | cut -d. -f1); \
	minor=$$(echo "$$version_string" | cut -d. -f2); \
	if [ "$$major" -lt 2 ] || ([ "$$major" -eq 2 ] && [ "$$minor" -lt 41 ]); then \
		echo "$(RED)❌ OpenAPI Generator CLI v$$version_string found, but v2.41.0+ required$(NC)"; \
		exit 1; \
	fi; \
	echo "$(GREEN)✅ OpenAPI Generator CLI v$$version_string OK$(NC)"

generate: validate check-bc check-generator-cli ## Generate Node SDK (requires Java 11+ JRE)
	@echo "$(BLUE)Generating Node SDK...$(NC)"
	@which java > /dev/null 2>&1 || (echo "$(RED)❌ Java Runtime (JRE) not found. Install Java 11+ JRE and try again$(NC)" && exit 1)
	npx openapi-generator-cli generate -c tools/generator-node-config.yaml
	@echo "$(GREEN)✅ Node SDK generated: generated/sdk-node-temp/$(NC)"

generate-all: validate check-bc check-generator-cli ## Generate all SDKs (requires Java 11+ JRE)
	@echo "$(BLUE)Generating all SDKs...$(NC)"
	@which java > /dev/null 2>&1 || (echo "$(RED)❌ Java Runtime (JRE) not found. Install Java 11+ JRE and try again$(NC)" && exit 1)
	npx openapi-generator-cli generate -c tools/generator-node-config.yaml
	npx openapi-generator-cli generate -c tools/generator-python-config.yaml
	npx openapi-generator-cli generate -c tools/generator-java-config.yaml
	npx openapi-generator-cli generate -c tools/generator-ruby-config.yaml
	npx openapi-generator-cli generate -c tools/generator-go-config.yaml
	npx openapi-generator-cli generate -c tools/generator-php-config.yaml
	npx openapi-generator-cli generate -c tools/generator-dotnet-config.yaml
	@echo "$(GREEN)✅ All SDKs generated!$(NC)"
	@echo ""
	@echo "Generated SDKs in: generated/"
	@ls -d generated/sdk-*-temp 2>/dev/null | sed 's|generated/sdk-||g; s|-temp||g' | sed 's/^/  - /'

# ============================================
# Cleanup
# ============================================

clean: ## Clean generated files and caches
	@echo "$(BLUE)Cleaning generated files...$(NC)"
	rm -rf generated/sdk-*-temp
	rm -rf node_modules
	rm -rf venv
	rm -f package-lock.json
	@echo "$(GREEN)✅ Clean complete$(NC)"

clean-all: clean ## Clean everything including Python venv
	rm -rf .npm
	@echo "$(GREEN)✅ Full clean complete$(NC)"

# ============================================
# Info & Status
# ============================================

status: ## Show project status
	@echo "$(BLUE)Project Status$(NC)"
	@echo ""
	@echo "Python:"
	@python3 --version 2>/dev/null || echo "  ❌ Not installed"
	@command -v openapi-spec-validator > /dev/null 2>&1 && echo "  ✅ openapi-spec-validator available" || echo "  ⚠️  openapi-spec-validator not available"
	@echo ""
	@echo "Node.js:"
	@node --version 2>/dev/null || echo "  ❌ Not installed"
	@npm --version 2>/dev/null || echo "  ❌ Not installed"
	@echo ""
	@echo "Java (for SDK generation):"
	@java -version 2>&1 | head -1 || echo "  ⚠️  Java not installed (required for make generate)"
	@echo ""
	@echo "Spec:"
	@if [ -f "apis/spec-v2.openapi.yaml" ]; then echo "  ✅ Found"; else echo "  ❌ Not found"; fi
	@echo ""
	@echo "Generated SDKs:"
	@if [ -d "generated" ]; then ls -d generated/sdk-*-temp 2>/dev/null | wc -l | xargs echo "  Count:"; else echo "  ❌ No generated SDKs"; fi

# ============================================
# Common Workflows
# ============================================

dev: validate check-bc ## Development workflow (validate + check BC)
	@echo "$(GREEN)✅ Development checks passed$(NC)"

ci: check ## CI workflow (all checks)
	@echo "$(GREEN)✅ CI checks passed$(NC)"

# ============================================
# Quick Commands
# ============================================

v: validate ## Shortcut: validate
check-all: check ## Shortcut: check all
gen: generate ## Shortcut: generate
