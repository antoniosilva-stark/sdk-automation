import yaml
import pytest

from conftest import REPO_ROOT

WORKFLOW_DIR = REPO_ROOT / ".github/workflows"
SYNC = WORKFLOW_DIR / "sdk-sync.yaml"
BATCH = WORKFLOW_DIR / "sdk-sync-batch.yaml"
DEFAULT_BRANCH_GUARD = "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"


def load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(workflow):
    # o YAML 1.1 lê a chave "on" como True
    return workflow.get("on") or workflow.get(True)


def testTheSyncCanBeCalledByAnotherWorkflowWithTheSameInputsPlusSkipTests():
    called = triggers(load(SYNC))["workflow_call"]["inputs"]

    assert called["resource"]["required"] is True
    assert called["language"]["type"] == "string"
    assert called["skipTests"]["type"] == "boolean"
    assert called["skipTests"]["default"] is False


def testAHumanDispatchOfTheSyncCannotSkipTheTests():
    dispatched = triggers(load(SYNC))["workflow_dispatch"]["inputs"]

    assert "skipTests" not in dispatched


SKIP_TESTS = "${{ !inputs.skipTests }}"
CACHE_ACTION = "actions/cache@5a3ec84eff668545956fd18022155c47e93e2684"  # v4.2.3


def servesOnlyTheTestSuite(step):
    run = step.get("run") or ""
    key = str((step.get("with") or {}).get("key", ""))
    return run == "make test" or "dependency:build-classpath" in run or key.startswith(("m2-validate-", "generator-validate-"))


def testOnlyTheStepsThatServeTheSuiteAreSkippedAndTheJobStillRuns():
    validate = load(SYNC)["jobs"]["validate"]
    steps = validate["steps"]
    serving = [step for step in steps if servesOnlyTheTestSuite(step)]

    assert "skipTests" not in (validate.get("if") or "")
    assert len(serving) == 4, "suite, classpath do Maven e os dois caches que so ela usa"
    assert all(step["if"] == SKIP_TESTS for step in serving)
    assert all("skipTests" not in str(step) for step in steps if step not in serving)


def testTheCachesUseOnePinnedActionAndKeysDerivedOnlyFromFiles():
    jobs = load(SYNC)["jobs"]
    found = 0
    for name in ("validate", "build", "compile"):
        for step in jobs[name]["steps"]:
            if "actions/cache" not in (step.get("uses") or ""):
                continue
            found += 1
            assert step["uses"].startswith(CACHE_ACTION), step["uses"]
            key = step["with"]["key"]
            assert "hashFiles(" in key and "inputs." not in key and "secrets." not in key, key
    assert found == 4


def testNoCacheReachesTheJobsThatHoldTheTokenAndEachJobKeepsItsOwnKeys():
    jobs = load(SYNC)["jobs"]
    keys = {name: [step["with"]["key"] for step in jobs[name]["steps"]
                   if "actions/cache" in (step.get("uses") or "")] for name in jobs}

    assert keys["deliver"] == [] and keys["drift-pr"] == [] and keys["drift"] == []
    assert len(keys["build"]) == 1 and "generator-build" in keys["build"][0], "o build nao roda maven"
    assert [key.split("-")[0] for key in keys["compile"]] == ["m2"] and "m2-compile-" in keys["compile"][0]
    assert not any("m2-compile-" in key for key in keys["validate"]), "chave do compile nao entra no validate"


def testTheBatchIsStartedOnlyByAHumanAndOnlyFromTheDefaultBranch():
    workflow = load(BATCH)

    assert set(triggers(workflow)) == {"workflow_dispatch"}
    for name in ("validate", "plan", "sync"):
        assert DEFAULT_BRANCH_GUARD in workflow["jobs"][name]["if"], name


def testTheBatchRunsTheSameValidationAsTheSyncOnce():
    syncSteps = load(SYNC)["jobs"]["validate"]["steps"]
    batchSteps = load(BATCH)["jobs"]["validate"]["steps"]
    syncSteps = [{key: value for key, value in step.items() if key != "if"} for step in syncSteps]

    assert batchSteps == syncSteps


def testNothingIsSyncedBeforeTheValidationAndThePlan():
    jobs = load(BATCH)["jobs"]

    assert "validate" in jobs["plan"]["needs"]
    assert set(jobs["sync"]["needs"]) == {"validate", "plan"}


def testTheSyncJobCallsTheSyncOncePerResourceInOrderAndKeepsGoingAfterAFailure():
    sync = load(BATCH)["jobs"]["sync"]

    assert sync["uses"] == "./.github/workflows/sdk-sync.yaml"
    assert sync["strategy"]["max-parallel"] == 1
    assert sync["strategy"]["fail-fast"] is False
    assert sync["strategy"]["matrix"]["resource"] == "${{ fromJSON(needs.plan.outputs.resources) }}"
    assert sync["with"]["resource"] == "${{ matrix.resource }}"
    assert sync["with"]["skipTests"] is True
    assert sync["secrets"] == "inherit"
    assert sync["permissions"] == {"contents": "read"}


def testThePlanJobHoldsNoSecretAndReadsTheInputsOnlyThroughEnv():
    plan = load(BATCH)["jobs"]["plan"]
    text = str(plan)

    assert "secrets." not in text
    assert plan["permissions"] == {"contents": "read"}
    runs = [step["run"] for step in plan["steps"] if "run" in step]
    assert any("plan-batch.py" in run for run in runs)
    assert not any("inputs." in run for run in runs)


@pytest.mark.parametrize("name", ["resources", "exclude", "language"])
def testTheBatchInputsExist(name):
    assert name in triggers(load(BATCH))["workflow_dispatch"]["inputs"]


def testTheBatchExcludesTheShapeTheToolRefusesByDefault():
    exclude = triggers(load(BATCH))["workflow_dispatch"]["inputs"]["exclude"]

    assert exclude["default"] == "CorporateCard"


def testTheBatchNeverMergesAnything():
    text = BATCH.read_text(encoding="utf-8")

    assert "gh pr merge" not in text
    assert "--auto" not in text


def testTheSyncOffersTheModeWithCreateAsTheDefaultInBothTriggers():
    triggered = triggers(load(SYNC))

    dispatched = triggered["workflow_dispatch"]["inputs"]["mode"]
    called = triggered["workflow_call"]["inputs"]["mode"]
    assert dispatched["options"] == ["create", "update"]
    assert dispatched["default"] == "create"
    assert called["default"] == "create"


def testTheBatchNeverAsksForAnUpdate():
    # Mutacao: repassar `mode: update` (ou um input mode no lote) faria a reescrita em massa sem decisao.
    batch = load(BATCH)
    call = batch["jobs"]["sync"]

    assert "mode" not in triggers(batch)["workflow_dispatch"]["inputs"]
    assert "mode" not in call["with"], "sem mode explicito, o sync usa o padrao create"
    assert "update" not in str(call)
    assert "inputs.mode" not in BATCH.read_text(encoding="utf-8")
