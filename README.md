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
Pull request
    ├─ validate-spec.yaml
    │   ├─ openapi-spec-validator (syntax, $ref, constraints)
    │   └─ breaking-change-detector.py (reports BC, does not block)
    └─ tests.yaml: jest + pytest
    ↓
Merge into development
    ↓
SDK Sync (sdk-sync.yaml) — manual dispatch from the default branch only, two inputs: resource + language (java only; Node is paused, see Languages)
    ├─ drift (no secrets): refresh the Python SDK, compare the versioned schema for this resource
    │     └─ drifted? drift-pr (holds the App token, runs no pip) opens a spec-update PR here
    │        and the sync is skipped for this resource only
    ├─ build (no secrets): validate the name, derive the ruler from the real target SDK, generate
    │     into staging/ and gate it against that ruler. Create-only: if the resource is already in
    │     the target SDK the build only measures parity, exits 4, the run reports it and compile and
    │     deliver are skipped (nothing is delivered). Otherwise upload staging/ as an artifact
    ├─ compile (no secrets): check out the public target SDK, put the generated file in, run
    │     mvn test-compile — the only place the target repo's own code runs
    └─ deliver (holds the App token, runs only this repo's stdlib tools): recompute the target repo,
          refuse if a declared path already exists on the base (prepare-branch --create-only),
          copy ONLY the declared paths from the artifact (symlink-safe, apply-artifacts.py), commit,
          push with --force-with-lease and open an "Add <resource> resource" PR in starkbank/sdk-<lang>
    ↓
Human reviews and merges → that repo's existing publishing pipeline takes over
```

Third-party code (generator, pip, npm, the target repo's Maven) never runs in a job that can see
the App secret: the jobs that hold the token only run this repo's tools and pinned actions.

## Tech stack — hybrid Python + Node.js

**Python 3.10+** — spec validation and all generation tooling
- `openapi-spec-validator` — the official OpenAPI validator, Python-only; this is why the
  stack is hybrid
- `tools/lint-spec.py` — classifies each spec resource as generatable or scaffolding
- `tools/breaking-change-detector.py` — BC detection in CI
- `tools/extract-schema.py` — derives an OpenAPI schema from the Stark Bank Python SDK
- `tools/build-resource.py` — generates a resource, verifies it, places it in the SDK layout
- `tools/place-generated.py` — name validation, target repo/layout mapping
- `tools/assert-generated.py` — gates generated output against the ruler
- `tools/derive-contract.py` — derives the ruler from the real target SDK, per language and role
- `tools/detect-drift.py` — compares the versioned schema with the current Python SDK
- `tools/list-gaps.py` — resources that exist in Python and not in the target SDK
- `tools/coverage-report.py` — how far each resource is from full parity

**Node.js 18+ locally, 22 in CI** — generation engine and JS tests
- OpenAPI Generator CLI: npm wrapper `^2.41.0`, core JAR pinned to `7.0.1` in
  `openapitools.json`
- Mustache templates per language, written to match each SDK's real public API rather than
  the generator defaults
- Jest

## Prerequisites

- **Python 3.10+**
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

`make setup` runs `make clone-sdk-ref`, which clones the remote HEAD of `sdk-python`, `sdk-java`
and `sdk-node` into `_references/`. Generation and drift detection read that HEAD on purpose.

The tests compare the versioned schemas with a fixed commit, so CI uses
`make clone-sdk-ref-pinned` (pins in the `Makefile`: `SDK_PYTHON_PIN`, `SDK_JAVA_PIN`,
`SDK_NODE_PIN`). Moving a pin and re-extracting the schemas (`apply-schema`) is one act.

Override with `make clone-sdk-ref SDK_PYTHON=/other/path`; an override is never touched.

> The workspace has two layers and the top one is **Stark Infra**, a different product:
> `~/workspace/sdk-<lang>` is `starkinfra/*`, `~/workspace/bank/sdk-<lang>` is `starkbank/*`.
> Only use repos resolved into `_references/`.

## Commands

```bash
make validate         # validate the OpenAPI spec (Python)          — alias: make v
make check-bc         # detect breaking changes (Python)
make test             # jest + pytest
make check            # validate + check-bc + test                  — alias: make check-all
make generate         # generate with the paused Node config (needs Java 11+ JRE) — alias: make gen
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
input may reach a `run:` block through `${{ }}`, every job declares `permissions`, the three
workflows agree on the Node major, every job of `sdk-sync.yaml` runs only from the default branch,
the jobs that run third-party code carry no secret, and the jobs that hold the token only run
this repo's tools and SHA-pinned actions.

In CI, `REQUIRE_TEST_DEPENDENCIES=1` turns a missing JDK, generator, SDK reference or the Maven
dependencies of the reference `sdk-java` (the runtime test of the generated constructor reads them
from the Maven cache) into a failure instead of a silent skip.

## Generation gates

Generating from an under-specified schema **does not fail** — it emits a plausible, compiling
class with correct CRUD methods and a single field. Five gates exist because of that:

1. **`lint-spec.py`** — splits the spec into generatable resources and scaffolding; `--require`
   fails when a named resource is not generatable
2. **`assert-generated.py`** — checks the generated artifact against the ruler, which
   `derive-contract.py` extracts from the real SDK file **on every run**. Rulers are per role
   (`main` in Java, `impl`/`barrel`/`types` in Node); an unknown kind fails the parse
   rather than being skipped. A resource with no ruler **fails** — only
   `--allow-missing-contract` lets a genuinely new resource through
3. **`--strict`** — ruler gaps become failures. `build-resource.py` passes it on every run
   unless `--advisory` is given, which exists for template development and is forbidden in the
   workflow
4. **`detect-drift.py`** — a resource whose versioned schema no longer matches the Python SDK
   is not generated; the run opens a spec-update PR instead, and the other resources proceed. It
   also compares the per-operation docstrings kept in each schema as `x-sdk-docs`
5. **`--style`** (`assert-generated.py`, Java, creation only) — blocks Python residue inside the
   Javadoc (`datetime.`, `None`, `True`, escaped quotes, `starkbank.user`), empty descriptions,
   trailing spaces, an orphan `@param`, a direct numeric cast in the Map constructor and a
   brace-less `if`/`for`; Python constructors in doc examples and unknown snake_case only warn

Divergence the generator cannot reach lives in `tests/waivers/<language>.waivers`: exact string,
mandatory reason, no wildcards. A waiver that stops matching is reported so it cannot rot
silently. Waivers apply when **filling a gap**; when the file already exists in the target SDK,
`--substitution` disables them all — losing an item there is a regression, not a pending item.

## Create-only

The pipeline only **creates** a resource that is absent from the target SDK. When the resource is
already there (all 42 are in `sdk-java` at the pinned commit), `build-resource.py` measures parity
with the strict gate in substitution mode, prints it, exits 4 and writes nothing; the workflow
records `intent=exists` and delivers nothing. Overwriting a hand-written file was removed because the
gate compares signatures, not bodies or docs: the first real dispatch produced a pull request that
kept every signature and still dropped documentation and changed constructor behaviour.

A generated file follows the dominant conventions measured in the real `sdk-java` (Javadoc on the
`data` field, constructor with return-only fields set to `null` and numerics read through `Number`,
method and overload order, imports). `tests/test_java_golden.py` compares the structure with the real
files for six resources and keeps a closed registry of divergences.

### Shapes the template cannot emit

Creating these is refused with `formato nao suportado` instead of emitting code that compiles and
calls the API wrongly: a `create` that returns a single object (Webhook, MerchantSession,
MerchantPurchase, CorporateCard, CorporateWithdrawal, CorporateInvoice, Workspace) and a singleton
`get` without an id (Balance, CorporateBalance). Sub-objects such as `Transfer.Rule`, untyped
`metadata` and a qualified `Transfer.Log` are known limits reported by the golden by severity.
All of this is the scope of the next phase (full-Java parity).

## Spec status — read this before trusting resource counts

`apis/spec-v2.openapi.yaml` declares 42 resources / 120 operations. Every resource is a `$ref`
to `apis/schemas/<resource>.yaml`, extracted from `sdk-python` with a provenance SHA. The 36
scaffolding resources (only `id` + `created`, with invented paths) were removed: generating from
one does not fail, it produces plausible and wrong code.

Declaring a resource does not make it generatable. Parity with the real SDKs is what the gate
measures, and it is still low (see above). Enriching the spec — not the templates — is the
bottleneck.
Note also that the real `Invoice` carries sub-objects (`Rule`, `Discount`, `Description`,
`Split`) and a `Log` sub-resource the spec does not describe, so full parity is not reachable
through Mustache alone.

## Languages

Seven generator configs exist in `tools/generator-{node,python,java,ruby,go,php,dotnet}-config.yaml`.

**Exercised end to end: `java`** — the only language `SDK Sync` accepts today.

**Node is paused.** The templates, the extractor and the tests stay, but the dispatch no longer
offers it: every resource tried against the derived ruler failed (1 to 13 gaps), and `sdk-node`
already has all 42 resources, so a Node run can only replace a file in production. Reactivate it
when the templates cover `put`, `query`, `page`, `delete` and `pdf`.

The other five configs generate default-shaped output and have neither templates nor contracts yet.

## Layout

```
apis/spec-v2.openapi.yaml          single source of truth (Stark Bank only)
apis/schemas/                      one file per resource (42), with a provenance comment
tools/                             Python tooling + 7 generator configs
templates/nodejs-{axios,impl,barrel,types}/, templates/java/
tests/                             pytest suites, unit/ for jest, contract/ for manifests
.github/workflows/                 validate-spec.yaml, tests.yaml, sdk-sync.yaml
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

The App needs Contents and Pull requests at read/write on `sdk-java` (and `sdk-node` once Node is reactivated). No
registry credentials are stored in this repo.

## Ownership

| Role | Who                                |
|---|------------------------------------|
| Tech-Lead | Elias — governance |
| Engineer | Antonio Silva and Nicolas Henriques|
| Spec Owner | not formally designated yet (`governance.md` lists Antonio Silva or Nicolas Almeida) |

See `governance.md` for the versioning rules, breaking-change policy and escalation path.

---
**Status**: POC — `SDK Sync` is merged into `development` and the first real dispatch ran; the
pipeline is create-only (see the Create-only section)
**Last updated**: 2026-10-16