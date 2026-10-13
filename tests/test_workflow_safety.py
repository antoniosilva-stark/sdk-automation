import re
import yaml
import subprocess
import pytest
from pathlib import Path

from conftest import REPO_ROOT

WORKFLOW_DIR = REPO_ROOT / ".github/workflows"
SYNC = WORKFLOW_DIR / "sdk-sync.yaml"
SAFE_IN_RUN = (
    "github.repository",
    "github.run_id",
    "github.run_number",
    "github.workflow",
    "github.job",
    "runner.os",
    "runner.temp",
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
            if not expression.startswith(SAFE_IN_RUN):
                found.append(f"{jobName}/{stepName}: {expression}")
    return found


def test_findsTheWorkflows():
    assert len(workflowFiles()) >= 2, "glob nao achou workflow — teste seria vacuo"


def test_theInjectionGuardIsAnAllowlist():
    perigosas = ("inputs.resource", "github.event.pull_request.title", "github.ref_name",
                 "github.ref", "github.base_ref", "github.head_ref", "github.actor",
                 "secrets.SDK_APP_PRIVATE_KEY", "secrets.GITHUB_TOKEN",
                 "steps.app-token.outputs.token", "needs.drift.outputs.blocked", "env.QUALQUER")
    for expression in perigosas:
        assert not expression.startswith(SAFE_IN_RUN), f"expressao perigosa liberada: {expression}"


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
    steps = syncSteps()
    copy = stepIndex(steps, "cp -R staging/.", "run")
    build = stepIndex(steps, "test-compile", "run")
    commit = stepIndex(steps, "git commit", "run")

    assert build >= 0, "nenhum step compila o gerado contra o SDK real"
    assert copy >= 0 and commit >= 0
    assert copy < build < commit, "compilacao fora da janela entre copy e commit"


def test_compilationCoversGeneratedTestAndRunsNothing():
    scripts = [step.get("run") or "" for step in syncSteps()]
    mvn = [script for script in scripts if "mvn" in script]

    assert mvn, "nenhuma invocacao de maven"
    for script in mvn:
        assert "test-compile" in script, "compile puro nao cobre o teste gerado"
        assert not re.search(r"mvn\s+(?:-\S+\s+)*test(?![-\w])", script), "mvn test pede credencial de sandbox"


def test_ownRepoCheckoutDoesNotPersistCredentials():
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
    scripts = [step.get("run") or "" for step in (driftJob().get("steps") or [])]
    refresh = next((i for i, script in enumerate(scripts) if "refresh-sdk-ref" in script), None)
    compare = next((i for i, script in enumerate(scripts) if "detect-drift.py" in script), None)

    assert refresh is not None, "job de defasagem compara sem atualizar a referencia"
    assert compare is not None
    assert refresh < compare


def test_specPrIsNeverAutoMerged():
    workflow = SYNC.read_text(encoding="utf-8")

    assert "gh pr merge" not in workflow
    assert "--auto" not in workflow


def test_specPrUsesTheAppTokenNotTheDefaultOne():
    steps = driftJob().get("steps") or []
    prStep = next((step for step in steps if "gh pr create" in (step.get("run") or "")), None)

    assert prStep is not None, "defasagem detectada e nenhuma PR de spec"
    assert "secrets.GITHUB_TOKEN" not in str(prStep)
    assert "steps.app-token.outputs.token" in str(prStep.get("env") or {})


@pytest.mark.parametrize("path", workflowFiles(), ids=lambda p: p.name)
def test_everyWorkflowDeclaresConcurrency(path):
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert "concurrency" in workflow, f"{path.name} sem concurrency"
    assert "group" in workflow["concurrency"]


def test_theSpecPullRequestIsNotDuplicated(tmpPath):
    steps = driftJob().get("steps") or []
    script = next(step["run"] for step in steps if "gh pr create" in (step.get("run") or ""))

    binario = tmpPath / "bin"
    binario.mkdir()
    chamadas = tmpPath / "chamadas.txt"
    (binario / "git").write_text(
        '#!/bin/sh\n'
        'case "$1 $2 $3" in "diff --cached --quiet") exit 1;; *) exit 0;; esac\n',
        encoding="utf-8")
    (binario / "gh").write_text(
        f'#!/bin/sh\necho "$@" >> {chamadas}\n'
        'case "$1 $2" in "pr list") echo 1;; *) ;; esac\n',
        encoding="utf-8")
    (binario / "python3").write_text("#!/bin/sh\necho split-profile\n", encoding="utf-8")
    for nome in ("git", "gh", "python3"):
        (binario / nome).chmod(0o755)

    resultado = subprocess.run(["/bin/bash", "-e", "-c", script], capture_output=True, text=True,
                               env={"PATH": f"{binario}:/usr/bin:/bin", "RESOURCE": "SplitProfile",
                                    "GH_TOKEN": "x", "BASE_BRANCH": "development",
                                    "GITHUB_REPOSITORY": "dono/repo"})

    executadas = chamadas.read_text(encoding="utf-8") if chamadas.exists() else ""
    assert "pr create" not in executadas, f"criou PR duplicada: {executadas}"
    assert resultado.returncode == 0, resultado.stderr


def test_theBreakingChangeGateLivesInThePullRequestOnly():
    for path in workflowFiles():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        usa = [f"{jobName}/{step.get('name') or step.get('run')}"
               for jobName, job in (workflow.get("jobs") or {}).items()
               for step in (job.get("steps") or [])
               if "breaking-change-detector" in (step.get("run") or "")]

        if path.name == "validate-spec.yaml":
            assert usa, "a PR precisa do gate de BC"
            continue
        assert usa == [], f"{path.name}: gate de BC fora da PR bloqueia sem caminho de aprovacao: {usa}"


def test_everyBranchCreationChecksTheRemoteFirst():
    for jobName, job in (yaml.safe_load(SYNC.read_text(encoding="utf-8"))["jobs"]).items():
        scripts = [step.get("run") or "" for step in (job.get("steps") or [])]
        empurra = next((i for i, script in enumerate(scripts)
                        if "git push" in script and "$BRANCH" in script), None)
        if empurra is None:
            continue

        protegido = any("ls-remote" in script or "--force-with-lease" in script
                        for script in scripts[:empurra + 1])
        assert protegido, f"{jobName}: push de branch sem checar o remoto antes"


def test_theStepThatReceivesTheAppTokenIsPinnedBySha():
    workflow = yaml.safe_load(SYNC.read_text(encoding="utf-8"))
    for jobName, job in (workflow.get("jobs") or {}).items():
        for step in (job.get("steps") or []):
            recebe = "app-token" in str(step.get("with") or {}) or "app-token" in str(step.get("env") or {})
            if not recebe or not step.get("uses"):
                continue
            referencia = step["uses"].split("@")[-1]
            assert _SHA.match(referencia), \
                f"{jobName}/{step.get('name') or step['uses']}: recebe o token do App em tag móvel"


def test_theApprovalFileRequiresReviewAndRetriggersTheGate():
    donos = REPO_ROOT / ".github/CODEOWNERS"
    assert donos.is_file(), "aprovacao de BC sem CODEOWNERS e autoassinada"
    assert "bc-approvals" in donos.read_text(encoding="utf-8")

    workflow = yaml.safe_load((WORKFLOW_DIR / "validate-spec.yaml").read_text(encoding="utf-8"))
    trigger = workflow["on"] if "on" in workflow else workflow[True]
    caminhos = trigger["pull_request"]["paths"]

    assert any("bc-approvals" in path for path in caminhos), \
        "PR que so adiciona aprovacao nao dispara o gate que a aprovacao dispensa"
