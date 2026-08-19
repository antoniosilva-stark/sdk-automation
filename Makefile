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
	@echo "Examples:"
	@echo "  make setup              # First-time setup (install all deps)"
	@echo "  make validate           # Validate OpenAPI spec"
	@echo "  make check-bc           # Detect breaking changes"
	@echo "  make generate           # Generate all SDKs"
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
	@echo "  make generate           # Generate SDKs"

install: install-python install-node ## Install Python + Node.js dependencies

install-python: ## Install Python dependencies (openapi-spec-validator)
	@echo "$(BLUE)Installing Python dependencies...$(NC)"
	@if [ ! -d "venv" ]; then python3 -m venv venv; fi
	@bash -c 'source venv/bin/activate && pip install -q -r requirements.txt'
	@echo "$(GREEN)✅ Python dependencies installed$(NC)"
	@echo "Activate venv: source venv/bin/activate"

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
# Validation & Checks
# ============================================

validate: ## Validate OpenAPI spec (Python)
	@echo "$(BLUE)Validating OpenAPI spec...$(NC)"
	@bash -c 'source venv/bin/activate && openapi-spec-validator apis/spec-v2.openapi.yaml'
	@echo "$(GREEN)✅ Spec validation passed$(NC)"

check-bc: ## Detect breaking changes (Python)
	@echo "$(BLUE)Checking for breaking changes...$(NC)"
	@bash -c 'source venv/bin/activate && python3 tools/breaking-change-detector.py'
	@echo "$(GREEN)✅ Breaking change check passed$(NC)"

check: validate check-bc ## Run all checks (validate + breaking-changes)
	@echo ""
	@echo "$(GREEN)✅ All checks passed!$(NC)"

# ============================================
# SDK Generation
# ============================================

generate: validate check-bc ## Generate all SDKs (after validation)
	@echo "$(BLUE)Generating SDKs...$(NC)"
	npm run generate-sdks
	@echo "$(GREEN)✅ SDK generation complete!$(NC)"
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
	@if [ -f "venv/bin/python" ]; then echo "  ✅ venv activated"; else echo "  ⚠️  venv not active"; fi
	@echo ""
	@echo "Node.js:"
	@node --version 2>/dev/null || echo "  ❌ Not installed"
	@npm --version 2>/dev/null || echo "  ❌ Not installed"
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
