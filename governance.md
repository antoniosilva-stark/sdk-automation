# Governance — SDK Automation

## Versionamento (Semver)

- **MAJOR**: Remove operation/schema
- **MINOR**: New resource/parameter
- **PATCH**: Bugfix

## Breaking-Change Policy

- Detected automatically: `breaking-change-detector.py` (Python) in CI
- CI reports every BC found; it does not block the merge (POC)

BC = removes operation, removes schema, removes required param, type changes, required field added, removes required field

`required field added` conta **apenas em schema de requisição** (o que `requestBody` alcança):
exigir campo novo quebra quem chama. Na resposta o acréscimo é promessa mais forte do
servidor e não quebra ninguém — o que quebra ali é o inverso, `removes required field`.

## Spec Owner

**Primary**: Antonio Silva (@antonio.silva) ou Nicolas Almeida (@nicolas.almeida)

Responsibilities:
- Keep spec.yaml in sync with Python SDK
- Review spec PRs
- Review BC changes reported by the detector

## Stack (Hybrid: Python + Node.js)

Formalizado em 2026-09-01. A automação usa as duas linguagens, cada uma pelo seu ponto forte:

- **Python 3.9+** — validação de spec (`openapi-spec-validator`, validador oficial OpenAPI) e detecção de breaking changes (`breaking-change-detector.py`)
- **Node.js 18+** — geração de SDKs (OpenAPI Generator CLI, templates Mustache) e testes (Jest)

## Tool Versions (Fixed)

- OpenAPI Generator (core/JAR): v7.0.1 — pinado via `openapitools.json` (upgrade deliberado)
- OpenAPI Spec: 3.1
- SDK references used by the tests: pinned by commit in the `Makefile` (`SDK_PYTHON_PIN`, `SDK_JAVA_PIN`, `SDK_NODE_PIN`); drift detection and generation read the remote HEAD

## PR Process (Spec Changes)

1. Branch: `feature/[resource-name]`
2. Update Python SDK (if needed)
3. Update spec.yaml (follow existing pattern)
4. Validate: `openapi-spec-validator apis/spec-v2.openapi.yaml`
5. Push & create PR
6. CI validates automatically (validate-spec.yaml)
7. Spec Owner reviews
8. Merge → sdk-sync.yaml auto-publishes (15-20 min)

## Escalation

| Role       | Name            | Escalation             |
|------------|-----------------|------------------------|
| Spec Owner | Antonio Silva   | → Spec Owner           |
| Spec Owner | Nicolas Almeida | → Tech-Lead (Elias)    |
| Tech-Lead  | Elias           | → Head of Integrations |
| Head       | Thiago Simon    | →                      |

---

**Version**: 1.0 | **Date**: 2026-08-19 | **Status**: Active