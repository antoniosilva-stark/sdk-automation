import re
import yaml
import pytest
from pathlib import Path

from conftest import REPO_ROOT

WORKFLOW_DIR = REPO_ROOT / ".github/workflows"
UNTRUSTED = ("inputs.", "github.event.")

_EXPRESSION = re.compile(r"\$\{\{\s*([^}]+?)\s*\}\}")

INJECTION_SAMPLE = """
jobs:
  build:
    steps:
      - name: Unsafe
        run: echo "${{ inputs.resource }}"
"""

ENV_SAMPLE = """
jobs:
  build:
    steps:
      - name: Safe
        env:
          RESOURCE: ${{ inputs.resource }}
        run: echo "$RESOURCE"
"""

OUTSIDE_RUN_SAMPLE = """
run-name: "${{ inputs.resource }}"
jobs:
  build:
    steps:
      - name: Safe
        if: ${{ inputs.resource != '' }}
        uses: some/action@v1
        with:
          resource: ${{ inputs.resource }}
"""


def workflowFiles() -> list[Path]:
    return sorted(WORKFLOW_DIR.glob("*.y*ml"))


def runBlocks(workflow: dict) -> list[tuple[str, str, str]]:
    blocks = []
    for jobName, job in (workflow.get("jobs") or {}).items():
        for index, step in enumerate(job.get("steps") or []):
            script = step.get("run")
            if not script:
                continue
            blocks.append((jobName, step.get("name") or f"step[{index}]", script))
    return blocks


def untrustedInRun(workflow: dict) -> list[str]:
    found = []
    for jobName, stepName, script in runBlocks(workflow):
        for expression in _EXPRESSION.findall(script):
            if expression.startswith(UNTRUSTED):
                found.append(f"{jobName}/{stepName}: {expression}")
    return found


def test_encontraOsWorkflows():
    assert len(workflowFiles()) >= 2, "glob nao achou workflow — teste seria vacuo"


@pytest.mark.parametrize("path", workflowFiles(), ids=lambda p: p.name)
def test_nenhumaEntradaNaoConfiavelDentroDeRun(path):
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    found = untrustedInRun(workflow)
    assert found == [], f"{path.name}: entrada interpolada em run:\n  " + "\n  ".join(found)


def test_detectorPegaInjecaoConhecida():
    assert untrustedInRun(yaml.safe_load(INJECTION_SAMPLE)) == ["build/Unsafe: inputs.resource"]


def test_detectorAceitaEntradaViaEnv():
    assert untrustedInRun(yaml.safe_load(ENV_SAMPLE)) == []


def test_detectorIgnoraExpressaoForaDeRun():
    assert untrustedInRun(yaml.safe_load(OUTSIDE_RUN_SAMPLE)) == []


def test_detectorEnxergaTodosOsStepsComRun():
    workflow = yaml.safe_load((WORKFLOW_DIR / "sdk-sync.yaml").read_text(encoding="utf-8"))
    assert len(runBlocks(workflow)) >= 5


def syncSteps() -> list[dict]:
    workflow = yaml.safe_load((WORKFLOW_DIR / "sdk-sync.yaml").read_text(encoding="utf-8"))
    return workflow["jobs"]["sync"]["steps"]


def stepIndex(steps: list[dict], needle: str, field: str) -> int:
    for index, step in enumerate(steps):
        if needle in (step.get(field) or ""):
            return index
    return -1


def test_geradorRodaAntesDoTokenExistir():
    steps = syncSteps()
    build = stepIndex(steps, "build-resource.py", "run")
    token = stepIndex(steps, "create-github-app-token", "uses")

    assert build >= 0, "step de build nao encontrado"
    assert token >= 0, "step de token nao encontrado"
    assert build < token, "gerador roda com o token do App em disco"


def test_checkoutDoProprioRepoNaoPersisteCredencial():
    first = syncSteps()[0]
    assert "actions/checkout" in first["uses"]
    assert first.get("with", {}).get("persist-credentials") is False


def test_buildNaoEscreveDiretoNoAlvo():
    steps = syncSteps()
    build = steps[stepIndex(steps, "build-resource.py", "run")]
    assert "--into staging" in build["run"]
    assert "--into target" not in build["run"]


def test_nenhumaSubstituicaoDeComandoDentroDeTeste():
    offenders = []
    for path in workflowFiles():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for jobName, stepName, script in runBlocks(workflow):
            for line in script.splitlines():
                if re.search(r"(?:if|while)\s+\[.*\$\(", line):
                    offenders.append(f"{path.name} {jobName}/{stepName}: {line.strip()}")
    assert offenders == [], "falha de comando engolida dentro de [ ]:\n  " + "\n  ".join(offenders)


def test_todoJobDeclaraPermissions():
    missing = []
    for path in workflowFiles():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for jobName, job in (workflow.get("jobs") or {}).items():
            if "permissions" not in job:
                missing.append(f"{path.name}/{jobName}")
    assert missing == [], "job sem permissions declarado: " + ", ".join(missing)


def test_nenhumJobPedeWriteAll():
    for path in workflowFiles():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for jobName, job in (workflow.get("jobs") or {}).items():
            assert job.get("permissions") != "write-all", f"{path.name}/{jobName}"


def test_comentarioEmPrCondicionaAStepEspecifico():
    for path in workflowFiles():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for jobName, job in (workflow.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                script = str(step.get("with", {}).get("script", ""))
                if "createComment" not in script:
                    continue
                condition = str(step.get("if", ""))
                assert "steps." in condition and ".outcome" in condition, (
                    f"{path.name}/{jobName}: comentario de PR condicionado a "
                    f"{condition!r} — atribui causa a qualquer falha anterior"
                )


SECRET_BEARING = ("create-github-app-token",)
_SHA = re.compile(r"^[0-9a-f]{40}$")


def actionRefs() -> list[tuple[str, str]]:
    refs = []
    for path in workflowFiles():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in (workflow.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                if step.get("uses"):
                    refs.append((path.name, step["uses"]))
    return refs


def test_acaoQueRecebeChavePrivadaEhPinadaPorSha():
    for fileName, uses in actionRefs():
        if not any(name in uses for name in SECRET_BEARING):
            continue
        _, _, ref = uses.partition("@")
        assert _SHA.match(ref), f"{fileName}: {uses} nao esta pinada por SHA"


def test_todoWorkflowUsaAMesmaMajorDeNode():
    versions = set()
    for path in workflowFiles():
        for match in re.finditer(r"node-version:\s*'(\d+)'", path.read_text(encoding="utf-8")):
            versions.add(match.group(1))
    assert len(versions) <= 1, f"majors divergentes entre workflows: {sorted(versions)}"


def test_quemRodaNpmCiConfiguraONode():
    for path in workflowFiles():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for jobName, job in (workflow.get("jobs") or {}).items():
            steps = job.get("steps") or []
            usesNpm = any("npm ci" in (s.get("run") or "") for s in steps)
            setsNode = any("actions/setup-node" in (s.get("uses") or "") for s in steps)
            assert not usesNpm or setsNode, f"{path.name}/{jobName}: npm ci sem setup-node"


def test_nenhumGitAddAmplo():
    offenders = []
    for path in workflowFiles():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for jobName, stepName, script in runBlocks(workflow):
            for line in script.splitlines():
                if re.search(r"git add\s+(--all|-A|\.)\s*$", line.strip()):
                    offenders.append(f"{path.name} {jobName}/{stepName}: {line.strip()}")
    assert offenders == [], "git add amplo:\n  " + "\n  ".join(offenders)


def test_baseEhConfirmadaContraORemotoNaoApenasContraHead():
    steps = syncSteps()
    base = steps[stepIndex(steps, 'echo "base=$BASE"', "run")]
    script = base["run"]

    assert "ls-remote --symref origin HEAD" in script, "base nao e confirmada no remoto"
    assert '"$BASE" != "$REMOTE_DEFAULT"' in script, "nada compara o checkout com a default real"
