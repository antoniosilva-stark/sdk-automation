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


def jobSteps(name: str) -> list[dict]:
    workflow = yaml.safe_load((WORKFLOW_DIR / "sdk-sync.yaml").read_text(encoding="utf-8"))
    return workflow["jobs"][name]["steps"]


def buildSteps() -> list[dict]:
    return jobSteps("build")


def deliverSteps() -> list[dict]:
    return jobSteps("deliver")


def stepIndex(steps: list[dict], needle: str, field: str) -> int:
    for index, step in enumerate(steps):
        if needle in (step.get(field) or ""):
            return index
    return -1


NO_SECRET_JOBS = ("drift", "build", "compile")
TOKEN_JOBS = ("drift-pr", "deliver")


@pytest.mark.parametrize("name", NO_SECRET_JOBS)
def test_thereIsNoTokenInTheJobsThatRunThirdPartyCode(name):
    job = yaml.safe_load(SYNC.read_text(encoding="utf-8"))["jobs"][name]
    raw = yaml.safe_dump(job)

    assert not re.search(r"secrets|toJSON|create-github-app-token|GH_TOKEN|x-access-token", raw, re.I), \
        f"{name}: o job que roda codigo de terceiros enxerga um secret ou token"
    assert job["permissions"] == {"contents": "read"}


DELIVER_ALLOWED_ACTIONS = ("actions/checkout@", "actions/download-artifact@", "actions/create-github-app-token@")


@pytest.mark.parametrize("name", TOKEN_JOBS)
def test_theJobsThatHoldTheTokenRunOnlyThisRepositoryCodeAndPinnedActions(name):
    job = yaml.safe_load(SYNC.read_text(encoding="utf-8"))["jobs"][name]

    for step in job["steps"]:
        used = step.get("uses")
        if used:
            assert used.startswith(DELIVER_ALLOWED_ACTIONS), f"{name}: action nao prevista no job com o token: {used}"
            assert re.search(r"@[0-9a-f]{40}\b", used), f"{name}: action sem SHA no job com o token: {used}"
        script = step.get("run") or ""
        for python in re.findall(r"python3\s+(\S+)", script):
            assert python.startswith(("tools/", "../tools/")), f"{name}: python fora de tools/: {python}"
        for forbidden in (r"\bpip ", r"\bnpm ", r"\bnpx ", r"\bmvn", r"\bgradle", r"\bmake ", r"\bnode ",
                          r"\bbash ", r"(?<![\w.])sh ", r"(?<![\w./])\./", "build-resource.py", "openapi-generator",
                          "detect-drift.py", "apply-schema.py"):
            assert not re.search(forbidden, script), f"{name}: o job com o token executa {forbidden}: {script.strip()[:80]}"


def test_theArtifactIsUploadedBeforeAnyTargetCodeExistsInTheJob():
    jobs = yaml.safe_load(SYNC.read_text(encoding="utf-8"))["jobs"]
    steps = jobs["build"]["steps"]

    upload = next(index for index, step in enumerate(steps) if "upload-artifact" in (step.get("uses") or ""))
    assert not any("repository" in (step.get("with") or {}) for step in steps), "build faz checkout do alvo"
    assert not any("mvn" in (step.get("run") or "") for step in steps), "build roda o maven do alvo"
    assert upload > stepIndex(steps, "build-resource.py", "run")
    assert not any("upload-artifact" in (step.get("uses") or "") for step in jobs["compile"]["steps"]), \
        "o job que roda o maven do alvo nao pode publicar artefato"


def test_deliverReadsFromTheBuildOnlyTheArtifactDigestAndNothingFromCompile():
    raw = yaml.safe_dump(yaml.safe_load(SYNC.read_text(encoding="utf-8"))["jobs"]["deliver"])

    used = set(re.findall(r"needs\.[\w-]+\.outputs\.[\w-]+", raw))
    assert used == {"needs.build.outputs.digest"}, f"o job com o token le saida de job que roda codigo de terceiros: {used}"


def test_deliverChecksTheArtifactDigestBeforeUsingAnyFileFromIt():
    steps = deliverSteps()
    verify = stepIndex(steps, "artifact-digest.py", "run")
    use = min(index for index, step in enumerate(steps) if "apply-artifacts.py" in (step.get("run") or ""))

    assert 0 <= verify < use
    assert stepIndex(steps, "--expect", "run") == verify
    jobs = yaml.safe_load(SYNC.read_text(encoding="utf-8"))["jobs"]
    fingerprint = stepIndex(jobs["build"]["steps"], "artifact-digest.py", "run")
    upload = next(i for i, step in enumerate(jobs["build"]["steps"]) if "upload-artifact" in (step.get("uses") or ""))
    assert fingerprint == upload - 1, "o resumo precisa ser calculado logo antes do upload"


def test_noJobCanRunAfterAFailureOrSkipOfItsDependencies():
    jobs = yaml.safe_load(SYNC.read_text(encoding="utf-8"))["jobs"]

    for name, job in jobs.items():
        condition = job.get("if") or ""
        assert not re.search(r"always\(|cancelled\(|failure\(|success\(", condition), \
            f"{name}: funcao de status troca o 'so roda se as dependencias passaram'"


def test_theDeclaredPathsFileIsWrittenBeforeTheTargetIsCheckedOutInEveryJobThatUsesIt():
    for name in ("compile", "deliver"):
        steps = jobSteps(name)
        declare = stepIndex(steps, "--targets", "run")
        checkout = next(index for index, step in enumerate(steps) if "repository" in (step.get("with") or {}))
        assert 0 <= declare < checkout, f"{name}: a lista tem de existir antes do checkout do alvo"


def test_theSpecPullRequestBodyIsFixedAndNeverTheDetectorLog():
    steps = driftPrJob()["steps"]
    script = steps[stepIndex(steps, "gh pr create", "run")]["run"]

    assert "--body-file" not in script, "o log do detector tem texto de fora e nao pode virar corpo de PR"


def test_theSpecRefreshBranchIsCheckedForStrayFilesBeforeItIsReused():
    steps = driftPrJob()["steps"]
    script = steps[stepIndex(steps, "gh pr create", "run")]["run"]

    assert script.index("--no-renames") < script.index("git reset -q"), "reset antes de checar a branch aberta"


def test_deliverOnlyDependsOnTheBuildAndReadsItsArtifact():
    workflow = yaml.safe_load(SYNC.read_text(encoding="utf-8"))
    deliver = workflow["jobs"]["deliver"]

    assert deliver["needs"] == ["build", "compile"]
    assert any("download-artifact" in (step.get("uses") or "") for step in deliver["steps"])


def test_deliverCopiesThroughTheSymlinkSafeToolWithAListFixedBeforeTheTarget():
    steps = deliverSteps()
    declare = stepIndex(steps, "--targets", "run")
    checkout = next(index for index, step in enumerate(steps) if "repository" in (step.get("with") or {}))
    copy = stepIndex(steps, "apply-artifacts.py", "run")

    assert 0 <= declare < checkout < copy, "a lista de caminhos tem de existir antes do checkout do alvo"
    assert not any("cp " in (step.get("run") or "") for step in steps), "cp segue link simbolico do alvo"


def test_targetIsCompiledAfterTheCopy():
    steps = jobSteps("compile")
    copy = stepIndex(steps, "cp -R staging/.", "run")
    build = stepIndex(steps, "test-compile", "run")

    assert build >= 0, "nenhum step compila o gerado contra o SDK real"
    assert copy >= 0
    assert copy < build, "compilacao antes de o gerado estar no alvo"


def test_compilationCoversGeneratedTestAndRunsNothing():
    scripts = [step.get("run") or "" for step in jobSteps("compile")]
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
    steps = buildSteps()
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
    steps = deliverSteps()
    base = steps[stepIndex(steps, 'echo "base=$BASE"', "run")]
    script = base["run"]

    assert "ls-remote --symref origin HEAD" in script, "base nao e confirmada no remoto"
    assert '"$BASE" != "$REMOTE_DEFAULT"' in script, "nada compara o checkout com a default real"


def driftJob() -> dict:
    workflow = yaml.safe_load(SYNC.read_text(encoding="utf-8"))
    return (workflow.get("jobs") or {}).get("drift") or {}


def driftPrJob() -> dict:
    workflow = yaml.safe_load(SYNC.read_text(encoding="utf-8"))
    return (workflow.get("jobs") or {}).get("drift-pr") or {}


def test_driftIsCheckedBeforeAnythingIsGenerated():
    workflow = yaml.safe_load(SYNC.read_text(encoding="utf-8"))
    jobs = workflow.get("jobs") or {}

    assert "drift" in jobs, "sem job de defasagem, gera-se a partir de spec velha"
    assert "drift" in (jobs["build"].get("needs") or [])
    scripts = [step.get("run") or "" for step in (jobs["drift"].get("steps") or [])]
    assert any("detect-drift.py" in script for script in scripts)


def test_syncIsSkippedOnlyForTheDriftedResource():
    workflow = yaml.safe_load(SYNC.read_text(encoding="utf-8"))
    condition = (workflow["jobs"]["build"].get("if") or "")

    assert "needs.drift.outputs" in condition
    assert "blocked" in condition


def test_driftJobResolvesTheCurrentReferenceBeforeComparing():
    scripts = [step.get("run") or "" for step in (driftJob().get("steps") or [])]
    resolve = next((i for i, script in enumerate(scripts) if "make clone-sdk-ref" in script), None)
    compare = next((i for i, script in enumerate(scripts) if "detect-drift.py" in script), None)

    assert resolve is not None, "job de defasagem compara sem resolver a referencia"
    assert "pinned" not in scripts[resolve], "drift le o HEAD de proposito, nao o pin"
    assert compare is not None
    assert resolve < compare


def test_specPrIsNeverAutoMerged():
    workflow = SYNC.read_text(encoding="utf-8")

    assert "gh pr merge" not in workflow
    assert "--auto" not in workflow


def test_specPrUsesTheAppTokenNotTheDefaultOne():
    steps = driftPrJob().get("steps") or []
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
    steps = driftPrJob().get("steps") or []
    script = next(step["run"] for step in steps if "gh pr create" in (step.get("run") or ""))

    (tmpPath / "paths.txt").write_text("apis/spec-v2.openapi.yaml\napis/schemas/splitprofile.yaml\n", encoding="utf-8")
    binario = tmpPath / "bin"
    binario.mkdir()
    chamadas = tmpPath / "chamadas.txt"
    (binario / "git").write_text(
        '#!/bin/sh\n'
        'case "$1 $2 $3" in "diff --cached --quiet") exit 1;; *) exit 0;; esac\n',
        encoding="utf-8")
    (binario / "gh").write_text(
        f'#!/bin/sh\necho "$@" >> {chamadas}\n'
        'case "$1" in api) echo 1;; *) ;; esac\n',
        encoding="utf-8")
    (binario / "python3").write_text("#!/bin/sh\necho split-profile\n", encoding="utf-8")
    for nome in ("git", "gh", "python3"):
        (binario / nome).chmod(0o755)

    resultado = subprocess.run(["/bin/bash", "-e", "-c", script], capture_output=True, text=True,
                               env={"PATH": f"{binario}:/usr/bin:/bin", "RESOURCE": "SplitProfile",
                                    "GH_TOKEN": "x", "BASE_BRANCH": "development",
                                    "GITHUB_REPOSITORY": "dono/repo", "RUNNER_TEMP": str(tmpPath)})

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
        assert usa == [], f"{path.name}: gate de BC fora da PR: {usa}"


def test_everyBranchCreationChecksTheRemoteFirst():
    for jobName, job in (yaml.safe_load(SYNC.read_text(encoding="utf-8"))["jobs"]).items():
        scripts = [step.get("run") or "" for step in (job.get("steps") or [])]
        empurra = next((i for i, script in enumerate(scripts)
                        if "git push" in script and "$BRANCH" in script), None)
        if empurra is None:
            continue

        protegido = any("ls-remote" in script or "--force-with-lease" in script or "prepare-branch.py" in script
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


def test_everyRemoteBranchProbeSeparatesAbsentFromUnreachable():
    for path in workflowFiles():
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for jobName, job in (workflow.get("jobs") or {}).items():
            for step in (job.get("steps") or []):
                script = step.get("run") or ""
                if "ls-remote --exit-code --heads" not in script:
                    continue
                assert "FOUND=$?" in script, \
                    f"{path.name}/{jobName}: falha de rede ou de acesso lida como branch ausente"


def test_aRegeneratedBranchIsOverwrittenOnlyAgainstTheShaThatWasRead():
    for path in workflowFiles():
        text = path.read_text(encoding="utf-8")

        assert "--force " not in text and "push -f" not in text, f"{path.name}: force push sem lease"
        for line in text.splitlines():
            if "--force-with-lease" in line:
                assert "--force-with-lease=" in line and "$PREVIOUS" in line, \
                    f"{path.name}: lease sem o SHA esperado: {line.strip()}"



DEFAULT_BRANCH_GUARD = "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"


def test_aDispatchFromAnotherRefFailsVisiblyInsteadOfFinishingEmpty():
    jobs = yaml.safe_load(SYNC.read_text(encoding="utf-8"))["jobs"]

    wrong = jobs["wrong-ref"]
    assert wrong["if"].startswith("github.ref != format("), "o job de aviso precisa disparar so fora da default"
    assert "exit 1" in wrong["steps"][0]["run"]
    assert wrong["permissions"] == {}


def test_everyJobRunsOnlyFromTheDefaultBranch():
    # o guard impede o disparo acidental; quem tem push ainda pode apagar o `if` na copia dele,
    # entao o limite real e o secret restrito por ambiente a default (config da org)
    jobs = yaml.safe_load(SYNC.read_text(encoding="utf-8"))["jobs"]

    for name, job in jobs.items():
        if name == "wrong-ref":
            continue
        condition = job.get("if") or ""
        startsWithGuard = condition == DEFAULT_BRANCH_GUARD or condition.startswith(DEFAULT_BRANCH_GUARD + " && ")
        assert startsWithGuard, f"job {name}: o guard de ref precisa ser a primeira condicao, ligada por &&"
        assert "||" not in condition, f"job {name}: um OR deixaria outra condicao liberar a ref"


def test_theBotIdentityTheWorkflowCommitsWithIsTheOnePrepareBranchTrusts():
    prepare = (REPO_ROOT / "tools/prepare-branch.py").read_text(encoding="utf-8")
    workflow = SYNC.read_text(encoding="utf-8")

    botEmail = re.search(r'BOT_EMAIL = "([^"]+)"', prepare).group(1)
    jobs = yaml.safe_load(workflow)["jobs"]
    for name in ("compile", "deliver"):
        scripts = "\n".join(step.get("run") or "" for step in jobs[name]["steps"])
        assert f'git config user.email "{botEmail}"' in scripts, f"{name}: sem a identidade do bot"
        assert scripts.index(f'git config user.email "{botEmail}"') < scripts.index("prepare-branch.py"), \
            f"{name}: a identidade precisa existir antes da preparacao"


def test_openPullRequestCheckFiltersByOwnerSoAForkCannotHideTheBotPr():
    for path in workflowFiles():
        raw = path.read_text(encoding="utf-8")
        assert "gh pr list" not in raw, f"{path.name}: gh pr list --head casa com PR de fork de mesmo nome"
    jobs = yaml.safe_load(SYNC.read_text(encoding="utf-8"))["jobs"]
    for name in ("drift-pr", "deliver"):
        scripts = "\n".join(step.get("run") or "" for step in jobs[name]["steps"])
        assert "head=$OWNER:$BRANCH" in scripts, f"{name}: filtro de PR sem o dono"


def test_remoteBranchProbesUseTheExactRef():
    raw = SYNC.read_text(encoding="utf-8")
    probes = re.findall(r'ls-remote[^\n]*"\$BRANCH"', raw)
    assert probes == [], f"sonda por sufixo: {probes}"


def test_theTokenCarryingPushesDisableLocalHooks():
    raw = SYNC.read_text(encoding="utf-8")
    pushes = [line for line in raw.splitlines() if "git " in line and " push " in line and "$REMOTE" in line or "x-access-token" in line and " push " in line]
    assert pushes, "nenhum push com token encontrado"
    for line in pushes:
        assert "core.hooksPath=/dev/null" in line, f"push com token sem desligar hooks: {line.strip()}"


@pytest.mark.parametrize("name", ["compile", "deliver"])
def test_aReusedBranchMayOnlyChangeTheDeclaredPaths(name):
    steps = jobSteps(name)
    script = steps[stepIndex(steps, "prepare-branch.py", "run")]["run"]

    assert '--allowed-paths-file "$RUNNER_TEMP/paths.txt"' in script, \
        f"{name}: commit alheio na branch reaproveitada viraria PR do bot"


@pytest.mark.parametrize("code,expectedExit", [("0", 0), ("1", 0), ("2", 1), ("3", 1)])
def test_theDriftComparisonOnlyProceedsWhenTheSchemaWasActuallyCompared(tmpPath, code, expectedExit):
    steps = driftJob()["steps"]
    script = steps[stepIndex(steps, "detect-drift.py", "run")]["run"]
    binario = tmpPath / "bin"
    binario.mkdir()
    (binario / "python3").write_text(f"#!/bin/sh\nexit {code}\n", encoding="utf-8")
    (binario / "python3").chmod(0o755)
    output = tmpPath / "output.txt"

    resultado = subprocess.run(["/bin/bash", "-e", "-c", script], capture_output=True, text=True, cwd=tmpPath,
                               env={"PATH": f"{binario}:/usr/bin:/bin", "RESOURCE": "SplitProfile",
                                    "GITHUB_OUTPUT": str(output)})

    assert resultado.returncode == expectedExit, resultado.stdout
    written = output.read_text(encoding="utf-8") if output.exists() else ""
    assert ("blocked=false" in written) == (code == "0"), "so 'em sincronia' libera a geracao"
    assert ("blocked=true" in written) == (code == "1")
