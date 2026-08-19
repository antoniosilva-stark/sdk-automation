# Governance — SDK Automation

## Versionamento (Semver)

- **MAJOR**: Remove operation/schema (requires Tech-Lead approval)
- **MINOR**: New resource/parameter (Spec Owner approval)
- **PATCH**: Bugfix (Spec Owner approval)

## Breaking-Change Policy

- Detected automatically: `breaking-change-detector.js` (Node.js) in CI
- CI blocks merge if BC found
- Requires Tech-Lead approval to merge BC changes
- SLA: 1-2h for approval

BC = removes operation, removes schema, removes required param, type changes, required field added

## Spec Owner

**Primary**: Antonio Silva (@antonio.silva)  
**Backup**: Nicolas Almeida (@nicolas.almeida)

Responsibilities:
- Keep spec.yaml in sync with Python SDK
- Review spec PRs
- Approve version bumps
- Validate BC changes

## Tool Versions (Fixed)

- OpenAPI Generator: v7.0.1 (upgrade with Tech-Lead approval)
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

| Role       | Name            | Escalation           |
|------------|-----------------|----------------------|
| Spec Owner | Antonio Silva   | → Backup             |
| Backup     | Nicolas Almeida | → Tech-Lead (Elias)  |
| Tech-Lead  | Elias           | → Head of Integrations |
| Head       | Thiago Simon    | →                    |

---

**Version**: 1.0 | **Date**: 2026-08-19 | **Status**: Active