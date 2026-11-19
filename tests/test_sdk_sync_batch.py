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


def testOnlyTheTestStepOfValidateIsSkippedAndTheJobStillRuns():
    validate = load(SYNC)["jobs"]["validate"]
    steps = validate["steps"]
    runTests = next(step for step in steps if step.get("run") == "make test")

    assert "skipTests" not in (validate.get("if") or "")
    assert runTests["if"] == "${{ !inputs.skipTests }}"
    assert all("skipTests" not in str(step) for step in steps if step is not runTests)


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
