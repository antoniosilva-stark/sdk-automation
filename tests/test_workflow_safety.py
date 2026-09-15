import re
import yaml
import pytest
from pathlib import Path

from conftest import REPO_ROOT

WORKFLOW_DIR = REPO_ROOT / ".github/workflows"
SYNC = WORKFLOW_DIR / "sdk-sync.yaml"
SAFE_IN_RUN = ("secrets.GITHUB_TOKEN",)
UNTRUSTED = (
    "inputs.",
    "github.event.",
    "github.ref_name",
    "github.head_ref",
    "github.actor",
    "github.triggering_actor",
    "secrets.",
    "steps.",
    "needs.",
    "env.",
)

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
            if expression.startswith(SAFE_IN_RUN):
                continue
            if expression.startswith(UNTRUSTED):
                found.append(f"{jobName}/{stepName}: {expression}")
    return found


def test_findsTheWorkflows():
    assert len(workflowFiles()) >= 2, "glob nao achou workflow — teste seria vacuo"


def test_theInjectionGuardCoversWhatTheWorkflowsActuallyUse():
    """A lista cobria so `inputs.` e `github.event.`, e o `${{ github.ref_name }}` que eu
    interpolei no step do token do App passava verde — com o README afirmando o contrario.
    """
    for expression in ("github.ref_name", "github.head_ref", "github.actor",
                       "secrets.SDK_APP_PRIVATE_KEY", "steps.app-token.outputs.token"):
        assert expression.startswith(UNTRUSTED), f"nao coberto pela guarda: {expression}"


@pytest.mark.parametrize("path", workflowFiles(), ids=lambda p: p.name)
def test_noUntrustedInputInsideRun(path):
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    found = untrustedInRun(workflow)
    assert found == [], f"{path.name}: entrada interpolada em run:\n  " + "\n  ".join(found)


def test_detectorCatchesKnownInjection():
    assert untrustedInRun(yaml.safe_load(INJECTION_SAMPLE)) == ["build/Unsafe: inputs.resource"]


def test_detectorAcceptsInputViaEnv():
    assert untrustedInRun(yaml.safe_load(ENV_SAMPLE)) == []


def test_detectorIgnoresExpressionOutsideRun():
    assert untrustedInRun(yaml.safe_load(OUTSIDE_RUN_SAMPLE)) == []


def test_detectorSeesEveryStepWithRun():
    workflow = yaml.safe_load(SYNC.read_text(encoding="utf-8"))
    assert len(runBlocks(workflow)) >= 5


def syncSteps() -> list[dict]:
    workflow = yaml.safe_load((WORKFLOW_DIR / "sdk-sync.yaml").read_text(encoding="utf-8"))
    return workflow["jobs"]["sync"]["steps"]


def stepIndex(steps: list[dict], needle: str, field: str) -> int:
    for index, step in enumerate(steps):
        if needle in (step.get(field) or ""):
            return index
    return -1


def test_generatorRunsBeforeTheTokenExists():
    steps = syncSteps()
    build = stepIndex(steps, "build-resource.py", "run")
    token = stepIndex(steps, "create-github-app-token", "uses")

    assert build >= 0, "step de build nao encontrado"
    assert token >= 0, "step de token nao encontrado"
    assert build < token, "gerador roda com o token do App em disco"


def test_targetIsCompiledBeforeTheCommit():
    """O sdk-java nao tem CI nenhuma: este gate e o unico que pega Java que nao compila.

    Tem de rodar depois do copy, porque so ali o pom.xml do alvo e o gerado coexistem,
    e antes do commit, para nao abrir PR com codigo que nao compila.
    """
    steps = syncSteps()
    copy = stepIndex(steps, "cp -R staging/.", "run")
    build = stepIndex(steps, "test-compile", "run")
    commit = stepIndex(steps, "git commit", "run")

    assert build >= 0, "nenhum step compila o gerado contra o SDK real"
    assert copy >= 0 and commit >= 0
    assert copy < build < commit, "compilacao fora da janela entre copy e commit"


def test_compilationCoversGeneratedTestAndRunsNothing():
    """`mvn compile` nao compila src/test — o teste gerado passaria sem verificacao.

    E `mvn test` exigiria PROJECT_ID/PROJECT_PRIVATE_KEY de sandbox, que este repo
    nao tem e nao deve ter.
    """
    scripts = [step.get("run") or "" for step in syncSteps()]
    mvn = [script for script in scripts if "mvn" in script]

    assert mvn, "nenhuma invocacao de maven"
    for script in mvn:
        assert "test-compile" in script, "compile puro nao cobre o teste gerado"
        assert not re.search(r"mvn\s+(?:-\S+\s+)*test(?![-\w])", script), "mvn test pede credencial de sandbox"


def test_ownRepoCheckoutDoesNotPersistCredentials():
    """Credencial ambiente de escrita e o que permite push acidental no proprio repo."""
    workflow = yaml.safe_load(SYNC.read_text(encoding="utf-8"))
    for jobName, job in (workflow.get("jobs") or {}).items():
        for step in (job.get("steps") or []):
            options = step.get("with") or {}
            if "actions/checkout" not in (step.get("uses") or "") or "repository" in options:
                continue
            assert options.get("persist-credentials") is False, \
                f"{jobName}: checkout do proprio repo mantem credencial de escrita"


def test_buildDoesNotWriteStraightIntoTheTarget():
    steps = syncSteps()
    build = steps[stepIndex(steps, "build-resource.py", "run")]
    assert "--into staging" in build["run"]
    assert "--into target" not in build["run"]


def test_noCommandSubstitutionInsideTest():
    offenders = []
    for path in workflowFiles():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for jobName, stepName, script in runBlocks(workflow):
            for line in script.splitlines():
                if re.search(r"(?:if|while)\s+\[.*\$\(", line):
                    offenders.append(f"{path.name} {jobName}/{stepName}: {line.strip()}")
    assert offenders == [], "falha de comando engolida dentro de [ ]:\n  " + "\n  ".join(offenders)


def test_everyJobDeclaresPermissions():
    missing = []
    for path in workflowFiles():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for jobName, job in (workflow.get("jobs") or {}).items():
            if "permissions" not in job:
                missing.append(f"{path.name}/{jobName}")
    assert missing == [], "job sem permissions declarado: " + ", ".join(missing)


def test_noJobAsksForWriteAll():
    for path in workflowFiles():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for jobName, job in (workflow.get("jobs") or {}).items():
            assert job.get("permissions") != "write-all", f"{path.name}/{jobName}"


def test_prCommentIsConditionedOnASpecificStep():
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


def test_actionReceivingPrivateKeyIsPinnedBySha():
    for fileName, uses in actionRefs():
        if not any(name in uses for name in SECRET_BEARING):
            continue
        _, _, ref = uses.partition("@")
        assert _SHA.match(ref), f"{fileName}: {uses} nao esta pinada por SHA"


def test_everyWorkflowUsesTheSameNodeMajor():
    versions = set()
    for path in workflowFiles():
        for match in re.finditer(r"node-version:\s*'(\d+)'", path.read_text(encoding="utf-8")):
            versions.add(match.group(1))
    assert len(versions) <= 1, f"majors divergentes entre workflows: {sorted(versions)}"


def test_whoeverRunsTestsResolvesTheReferenceFirst():
    """47 testes pytest ficavam skipped no CI por falta de _references/sdk-python.

    O golden, o teste de gap e o de cobertura estavam entre eles: o CI nao verificava
    fidelidade ao SDK Python, que e o proposito do projeto.
    """
    for path in workflowFiles():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for jobName, job in (workflow.get("jobs") or {}).items():
            steps = job.get("steps") or []
            scripts = [step.get("run") or "" for step in steps]

            testIndex = next((i for i, script in enumerate(scripts) if "make test" in script), None)
            if testIndex is None:
                continue

            cloneIndex = next((i for i, script in enumerate(scripts) if "clone-sdk-ref" in script), None)
            assert cloneIndex is not None, f"{path.name}:{jobName} roda teste sem resolver a referencia"
            assert cloneIndex < testIndex


def test_whoeverBuildsResolvesTheReferenceFirst():
    """A regua e derivada do SDK real a cada execucao: sem a referencia nao ha o que medir.

    Roda antes do token do App de proposito (decisao 29): os SDKs sao publicos.
    """
    for path in workflowFiles():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for jobName, job in (workflow.get("jobs") or {}).items():
            scripts = [step.get("run") or "" for step in (job.get("steps") or [])]

            buildIndex = next((i for i, script in enumerate(scripts) if "build-resource.py" in script), None)
            if buildIndex is None:
                continue

            cloneIndex = next((i for i, script in enumerate(scripts) if "clone-sdk-ref" in script), None)
            assert cloneIndex is not None, f"{path.name}:{jobName} gera sem resolver a referencia"
            assert cloneIndex < buildIndex


def test_whoeverRunsNpmCiSetsUpNode():
    for path in workflowFiles():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for jobName, job in (workflow.get("jobs") or {}).items():
            steps = job.get("steps") or []
            usesNpm = any("npm ci" in (s.get("run") or "") for s in steps)
            setsNode = any("actions/setup-node" in (s.get("uses") or "") for s in steps)
            assert not usesNpm or setsNode, f"{path.name}/{jobName}: npm ci sem setup-node"


def test_noBroadGitAdd():
    offenders = []
    for path in workflowFiles():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for jobName, stepName, script in runBlocks(workflow):
            for line in script.splitlines():
                if re.search(r"git add\s+(--all|-A|\.)\s*$", line.strip()):
                    offenders.append(f"{path.name} {jobName}/{stepName}: {line.strip()}")
    assert offenders == [], "git add amplo:\n  " + "\n  ".join(offenders)


def test_baseIsConfirmedAgainstTheRemoteNotJustHead():
    steps = syncSteps()
    base = steps[stepIndex(steps, 'echo "base=$BASE"', "run")]
    script = base["run"]

    assert "ls-remote --symref origin HEAD" in script, "base nao e confirmada no remoto"
    assert '"$BASE" != "$REMOTE_DEFAULT"' in script, "nada compara o checkout com a default real"


def driftJob() -> dict:
    workflow = yaml.safe_load(SYNC.read_text(encoding="utf-8"))
    return (workflow.get("jobs") or {}).get("drift") or {}


def test_driftIsCheckedBeforeAnythingIsGenerated():
    """Decisao 61: recurso defasado espera; os outros seguem. O bloqueio e por recurso."""
    workflow = yaml.safe_load(SYNC.read_text(encoding="utf-8"))
    jobs = workflow.get("jobs") or {}

    assert "drift" in jobs, "sem job de defasagem, gera-se a partir de spec velha"
    assert "drift" in (jobs["sync"].get("needs") or [])
    scripts = [step.get("run") or "" for step in (jobs["drift"].get("steps") or [])]
    assert any("detect-drift.py" in script for script in scripts)


def test_syncIsSkippedOnlyForTheDriftedResource():
    workflow = yaml.safe_load(SYNC.read_text(encoding="utf-8"))
    condition = (workflow["jobs"]["sync"].get("if") or "")

    assert "needs.drift.outputs" in condition
    assert "blocked" in condition


def test_driftJobRefreshesTheReferenceBeforeComparing():
    """Decisao 62: o fetch vive no sdk-sync. Comparar contra clone velho e nao comparar."""
    scripts = [step.get("run") or "" for step in (driftJob().get("steps") or [])]
    refresh = next((i for i, script in enumerate(scripts) if "refresh-sdk-ref" in script), None)
    compare = next((i for i, script in enumerate(scripts) if "detect-drift.py" in script), None)

    assert refresh is not None, "job de defasagem compara sem atualizar a referencia"
    assert compare is not None
    assert refresh < compare


def test_specPrIsNeverAutoMerged():
    """A spec e o artefato que a governanca de BC protege: PR de spec e revisada por gente."""
    workflow = SYNC.read_text(encoding="utf-8")

    assert "gh pr merge" not in workflow
    assert "--auto" not in workflow


def test_specPrUsesTheAppTokenNotTheDefaultOne():
    """PR aberta com GITHUB_TOKEN nao dispara workflow nenhum: a PR nasceria sem validacao."""
    steps = driftJob().get("steps") or []
    prStep = next((step for step in steps if "gh pr create" in (step.get("run") or "")), None)

    assert prStep is not None, "defasagem detectada e nenhuma PR de spec"
    assert "secrets.GITHUB_TOKEN" not in str(prStep)
    assert "steps.app-token.outputs.token" in str(prStep.get("env") or {})
