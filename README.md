# SDK Automation POC

Automate generation and publishing of SDKs across 7 languages from a single OpenAPI 3.1 specification.

## The Goal
- Spec validation & breaking-change detection
- SDK generation (parallel: 7 SDKs)
- Testing (parallel: 7 SDKs)
- Publishing (sequential: 9 registries)

## Tech Stack

### Hybrid Architecture: Python + Node.js

**Python 3.9+** — Spec Validation
- Validate OpenAPI 3.1 spec completeness
- Check constraints (types, formats, patterns)
- Guard CI/CD pipeline quality
- Tool: `openapi-spec-validator` (official OpenAPI validator)

**Node.js 14+** — SDK Generation & Tooling
- Manage project dependencies (`npm`)
- Generate SDKs via OpenAPI Generator CLI
- Tool: `breaking-change-detector.py` (custom detector)
- Fast CI/CD integration

### Why Hybrid?
- Python has the **official OpenAPI validator** (most robust)
- Node.js has **native npm support** for CLI tools
- **Result**: Best validation + best generation

---

## Getting Started

### Prerequisites
- Node.js 14+
- Python 3.9+
- Git
- Bash shell

### 1. Clone Repository

```bash
git clone https://github.com/starkbank/sdk-automation.git
cd sdk-automation

# Or if already in repo
git pull origin main
```

### 2. Install Dependencies

### Using Make - Available Commands (Recommended)

```bash
# First-time setup
make setup

# Validate spec
make validate   # or: make v

# Check for breaking changes
make check-bc

# Run all checks
make check      # or: make check-all

# Generate all SDKs
make generate   # or: make gen

# Show project status
make status

# Clean generated files
make clean

# Show all available commands
make help
```

**Python** (for OpenAPI validation):
```bash
# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install Python dependencies
pip install -r requirements.txt
```

**Node.js** (for SDK generation and breaking-change detection):
```bash
npm install
```

### 3. Clone Python SDK Reference

```bash
make clone-sdk-ref
# usa $(HOME)/workspace/bank/sdk-python se existir; senao clona do GitHub
# sobrescreva com: make clone-sdk-ref SDK_PYTHON=/outro/caminho
```

### 4. Verify Setup

```bash
# Check OpenAPI spec validator (Python)
npm run validate-spec
# Expected: "apis/spec-v2.openapi.yaml: OK"

# Check breaking change detector (Node.js)
npm run check-breaking-changes
# Expected: "✅ No breaking changes detected" (on first run)
```

### 5. Install OpenAPI Generator

```bash
# Only needed for SDK generation
npm install -g @openapitools/openapi-generator-cli@7.0.1

# Verify installation
openapi-generator-cli version
# Expected: 7.0.1
```

### Using npm directly

```bash
# Validate OpenAPI spec (Python)
npm run validate-spec

# Detect breaking changes (Node.js)
npm run check-breaking-changes

# Generate all SDKs
npm run generate-sdks
```

### Commands Explained

**validate** (Python) — Validates OpenAPI 3.1 spec
- ✅ Checks syntax
- ✅ Validates constraints (types, formats, patterns)
- ✅ Validates schema references ($ref)
- ✅ Runs in CI/CD pipeline (PR validation)

**check-bc** (Node.js) — Detects breaking changes
- ✅ Compares current vs previous spec
- ✅ Detects removed operations/schemas
- ✅ Detects type changes
- ✅ Runs in CI/CD pipeline (PR validation)

**generate** — Generates all SDKs
- ✅ Validates spec first (Python)
- ✅ Generates 7 SDKs in parallel
- ✅ Outputs to `generated/sdk-{lang}-temp/`


## Key Files

- **governance.md** — Versioning, breaking-change policy, ownership
- **apis/spec-v2.openapi.yaml** — OpenAPI 3.1 spec (60+ resources)
- **MASTER_CONTEXT.md** — Detailed project documentation

## Workflow

```
Developer updates spec
    ↓
Push to main
    ↓
GitHub Actions: validate-spec.yaml (PR)
├─ Validate spec syntax
├─ Detect breaking changes
└─ Block merge if invalid/BC
    ↓
GitHub Actions: sdk-sync.yaml (on main)
├─ Generate 7 SDKs (parallel)
├─ Test 7 SDKs (parallel)
├─ Publish to 9 registries (sequential)
    ↓ (~15-20 min)
All SDKs updated ✅
```

## Supported Languages

- Node.js (npm)
- Python (PyPI)
- Java (Maven Central)
- Ruby (RubyGems)
- Go (GitHub Releases)
- PHP (Packagist)
- Dotnet (NuGet)

## Contact

**Spec Owner**: Antonio Silva  
**Tech-Lead**: Elias  
**Backup**: Nicolas Almeida

See governance.md for escalation policy.

---
**Status**: POC in progress (Entrega 1)  
**Last updated**: 2026-08-19