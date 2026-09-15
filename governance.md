# Governance — SDK Automation

## Versionamento (Semver)

- **MAJOR**: Remove operation/schema (requires Tech-Lead approval)
- **MINOR**: New resource/parameter (Spec Owner approval)
- **PATCH**: Bugfix (Spec Owner approval)

## Breaking-Change Policy

- Detected automatically: `breaking-change-detector.py` (Python) in CI
- CI blocks merge if BC found
- Requires Tech-Lead approval to merge BC changes
- SLA: 1-2h for approval

BC = removes operation, removes schema, removes required param, type changes, required field added, removes required field

`required field added` conta **apenas em schema de requisição** (o que `requestBody` alcança):
exigir campo novo quebra quem chama. Na resposta o acréscimo é promessa mais forte do
servidor e não quebra ninguém — o que quebra ali é o inverso, `removes required field`.

Aprovação de BC é registrada em `apis/bc-approvals.yaml`: string exata, motivo e quem aprovou.
O detector subtrai as aprovadas e reporta aprovação que deixou de casar com algo.

## Spec Owner

**Primary**: Antonio Silva (@antonio.silva) ou Nicolas Almeida (@nicolas.almeida)

Responsibilities:
- Keep spec.yaml in sync with Python SDK
- Review spec PRs
- Approve version bumps
- Validate BC changes

## Stack (Hybrid: Python + Node.js)

Formalizado em 2026-09-01. A automação usa as duas linguagens, cada uma pelo seu ponto forte:

- **Python 3.9+** — validação de spec (`openapi-spec-validator`, validador oficial OpenAPI) e detecção de breaking changes (`breaking-change-detector.py`)
- **Node.js 18+** — geração de SDKs (OpenAPI Generator CLI, templates Mustache) e testes (Jest)

## Tool Versions (Fixed)

- OpenAPI Generator (core/JAR): v7.0.1 — pinado via `openapitools.json` (upgrade com aprovação do Tech-Lead)
- OpenAPI Spec: 3.1
- Python SDK Reference: v0.28.0

## PR Process (Spec Changes)

1. Branch: `feature/[resource-name]`
2. Update Python SDK (if needed)
3. Update spec.yaml (follow existing pattern)
4. Validate: `openapi-spec-validator apis/spec-v2.openapi.yaml`
5. Push & create PR (tag @elias if BC)
6. CI validates automatically (validate-spec.yaml)
7. Spec Owner reviews + Tech-Lead approves
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