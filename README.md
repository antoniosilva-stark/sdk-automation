# SDK Automation POC

Generate Stark Bank SDK code from a single OpenAPI 3.1 spec and **open a pull request in the
target SDK repository**.

This repo does **not** publish to registries. Each `starkbank/sdk-<lang>` repo already has its
own publishing pipeline, which takes over after a human reviews and merges the generated PR —
so no npm/PyPI/Maven/NuGet credentials exist here.

**Goal**: cut the cost of closing a feature gap across SDKs from ~18 person-days to ~1
person-day plus 15-20 minutes of automation.

## Flow

```
Developer edits apis/spec-v2.openapi.yaml
    ↓
Pull request → validate-spec.yaml
    ├─ openapi-spec-validator (syntax, $ref, constraints)
    ├─ breaking-change-detector.py (blocks merge on BC without Tech-Lead approval)
    └─ jest + pytest
    ↓
Merge into development
    ↓
SDK Sync (sdk-sync.yaml) — manual dispatch, two inputs: resource + language (java | node)
    ├─ validate resource name at the edge (place-generated.py --slug)
    ├─ resolve target repo from the language (place-generated.py --repo)
    ├─ generate into staging/ and verify against the contract  ← runs BEFORE the token exists
    ├─ mint a short-lived GitHub App token
    ├─ copy staging/ over the target checkout, stage only declared paths (--targets)
    └─ open a PR in starkbank/sdk-<lang>
    ↓
Human reviews and merges → that repo's existing publishing pipeline takes over
```

The generator runs before the App token is minted, so third-party template/generator code
never executes with a credential on disk.

## Tech stack — hybrid Python + Node.js

**Python 3.9+** — spec validation and all generation tooling
- `openapi-spec-validator` — the official OpenAPI validator, Python-only; this is why the
  stack is hybrid
- `tools/lint-spec.py` — classifies each spec resource as generatable or scaffolding
- `tools/breaking-change-detector.py` — BC detection in CI
- `tools/extract-schema.py` — derives an OpenAPI schema from the Stark Bank Python SDK
- `tools/build-resource.py` — generates a resource, verifies it, places it in the SDK layout
- `tools/place-generated.py` — name validation, target repo/layout mapping
- `tools/assert-generated.py` — gates generated output against a contract

**Node.js 18+ locally, 22 in CI** — generation engine and JS tests
- OpenAPI Generator CLI: npm wrapper `^2.41.0`, core JAR pinned to `7.0.1` in
  `openapitools.json`
- Mustache templates per language, written to match each SDK's real public API rather than
  the generator defaults
- Jest

## Prerequisites

- **Python 3.9+**
- **Node.js 18+** (CI runs 22)
- **Java 11+ JRE** — required by OpenAPI Generator CLI. `make generate` aborts without it.
  On macOS: `brew install openjdk@17`, then put it on `PATH` (the formula is keg-only).
- Git, Bash

## Setup

```bash
git clone https://github.com/starkbank/sdk-automation.git
cd sdk-automation
git checkout development        # default branch

make setup                      # installs Python + Node deps, resolves the SDK reference
make status                     # shows which prerequisites are present
```

`make setup` runs `make clone-sdk-ref`, which symlinks `_references/sdk-python` to
`$HOME/workspace/bank/sdk-python` when present and clones from GitHub otherwise (so CI works).
Override with `make clone-sdk-ref SDK_PYTHON=/other/path`.

> The workspace has two layers and the top one is **Stark Infra**, a different product:
> `~/workspace/sdk-<lang>` is `starkinfra/*`, `~/workspace/bank/sdk-<lang>` is `starkbank/*`.
> Only use repos resolved into `_references/`.

## Commands

```bash
make validate         # validate the OpenAPI spec (Python)          — alias: make v
make check-bc         # detect breaking changes (Python)
make test             # jest + pytest
make check            # validate + check-bc + test                  — alias: make check-all
make generate         # generate the Node SDK (needs Java 11+ JRE)  — alias: make gen
make generate-all     # generate all 7 configured languages, sequentially
make status           # prerequisites, spec presence, generated count
make clean            # remove generated output and caches
make help             # every target
```

npm scripts are thin wrappers over the same things: `npm run validate-spec`,
`npm run check-breaking-changes`, `npm test`, `npm run generate-sdks` (→ `make generate`).

Generating a single resource the way CI does it, including the contract gate:

```bash
python3 tools/build-resource.py SplitProfile --lang java --into /path/to/sdk-java
```


`test_workflow_safety.py` parses the workflow YAML and asserts invariants on it — no workflow
input may reach a `run:` block through `${{ }}`, every job declares `permissions`, both
workflows agree on the Node major, and any action receiving a private key is pinned by SHA.

## Generation gates

Generating from an under-specified schema **does not fail** — it emits a plausible, compiling
class with correct CRUD methods and a single field. Three gates exist because of that:

1. **`lint-spec.py`** — splits the spec into generatable resources and scaffolding; `--require`
   fails when a named resource is not generatable
2. **`assert-generated.py`** — checks the generated artifact against a contract derived from
   the real SDK file. Contracts are **per role** (`impl`, `barrel`, `types`), not per resource;
   an unknown contract kind fails the parse rather than being skipped
3. **`--strict`** — contract gaps become failures. `build-resource.py` passes it on every run

Contracts carry two levels: blocking kinds, and `todo` entries that are parsed, counted and
announced on every run but never block. The `todo` level exists so that "0 gaps" cannot be
read as full parity — 45 declared parity items (30 in Java `Invoice`, 15 in Node `Transfer`)
are known gaps the generator does not reach.

## Spec status — read this before trusting resource counts

`apis/spec-v2.openapi.yaml` declares 60 resources / 180 operations and holds 120 schemas.
Measured schema by schema, **4 resources are actually modelled**:

| Group | Count | Content |
|---|---|---|
| `$ref` → `apis/schemas/*.yaml` | 6 | `Transaction`, `Invoice`, `Transfer` × (`X` + `XCreate`) |
| written by hand for the pilot | 2 | `SplitProfile` (7 props) + `SplitProfileCreate` (3) |
| stub `X` | 56 | only `id` + `created` |
| stub `XCreate` | 56 | empty |

The remaining 56 are scaffolding. Enriching the spec — not the templates — is the bottleneck.
Note also that the real `Invoice` carries sub-objects (`Rule`, `Discount`, `Description`,
`Split`) and a `Log` sub-resource the spec does not describe, so full parity is not reachable
through Mustache alone.

## Languages

Seven generator configs exist in `tools/generator-{node,python,java,ruby,go,php,dotnet}-config.yaml`.

**Exercised end to end: `node` and `java`** — those are the two `SDK Sync` accepts today, and
the only ones with templates matching the real SDK API. The other five configs generate
default-shaped output and have neither templates nor contracts yet.

## Layout

```
apis/spec-v2.openapi.yaml          single source of truth (Stark Bank only)
apis/schemas/                      transaction.yaml, invoice.yaml, transfer.yaml
tools/                             Python tooling + 7 generator configs
templates/nodejs-{axios,impl,barrel,types}/, templates/java/
tests/                             pytest suites, unit/ for jest, contract/ for manifests
.github/workflows/                 validate-spec.yaml, sdk-sync.yaml
docs/NODE_GENERATION_GUIDE.md      how Node generation is wired
generated/                         local output (gitignored)
_references/                       real SDK clones used as reference (gitignored, read-only)
governance.md                      versioning, BC policy, ownership
openapitools.json                  pins the generator core to 7.0.1
```

## Secrets

`SDK Sync` needs a GitHub App, not a personal token, so the pipeline does not break when
someone leaves:

| Secret | Purpose |
|---|---|
| `SDK_APP_ID` | GitHub App that opens PRs in the SDK repos |
| `SDK_APP_PRIVATE_KEY` | its private key; the workflow mints a token scoped per run |

The App needs Contents and Pull requests at read/write on `sdk-java` and `sdk-node`. No
registry credentials are stored in this repo.

## Ownership

| Role | Who                                |
|---|------------------------------------|
| Tech-Lead | Elias — governance, approvals, BC decisions |
| Engineer | Antonio Silva and Nicolas Henriques|
| Spec Owner | not formally designated yet (`governance.md` lists Antonio Silva or Nicolas Almeida) |

See `governance.md` for the versioning rules, breaking-change policy and escalation path.

---
**Status**: POC — pilot (`SplitProfile` / Java) generated, verified and proven end to end;
waiting on review to reach `development`
**Last updated**: 2026-09-11