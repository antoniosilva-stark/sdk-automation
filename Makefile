.PHONY: help setup install validate check-bc generate clean

# Colors for output
GREEN := \033[0;32m
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

clone-sdk-ref: ## Clone Python SDK v0.28.0 as reference
	@echo "$(BLUE)Cloning Python SDK reference...$(NC)"
	mkdir -p _references
	@if [ ! -d "_references/sdk-python" ]; then \
		git clone -q https://github.com/starkbank/sdk-python.git _references/sdk-python; \
		echo "$(GREEN)✅ Python SDK reference cloned$(NC)"; \
	else \
		echo "$(GREEN)✅ Python SDK reference already exists$(NC)"; \
	fi

# ============================================
# Tests
# ============================================

test: ## Run all unit tests (Jest)
	@echo "$(BLUE)Running all unit tests...$(NC)"
	npm test
	@echo "$(GREEN)✅ All tests passed$(NC)"

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
