import json
import importlib.util

import pytest

from conftest import TOOLS_DIR


def loadPlan():
    spec = importlib.util.spec_from_file_location("plan_batch", TOOLS_DIR / "plan-batch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plan = loadPlan()

KNOWN = ["Balance", "Boleto", "CorporateCard", "CorporateHolder"]


def inOrder(wanted):
    return sorted(wanted), []


def runPlan(argv, order=inOrder, known=KNOWN):
    return plan.main(argv=argv, order=order, known=known)


def testWithoutArgumentsItPlansEveryKnownResourceAsAJsonList(capsys):
    code = runPlan(["--lang", "java"])

    assert code == 0
    assert json.loads(capsys.readouterr().out) == sorted(KNOWN)


def testTheRequestedListNarrowsThePlanAndIgnoresBlanks(capsys):
    code = runPlan(["--lang", "java", "--resources", " Boleto, ,Balance"])

    assert code == 0
    assert json.loads(capsys.readouterr().out) == ["Balance", "Boleto"]


def testExcludedResourcesLeaveThePlan(capsys):
    code = runPlan(["--lang", "java", "--exclude", "CorporateCard"])

    assert code == 0
    assert "CorporateCard" not in json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("flag", ["--resources", "--exclude"])
def testANameOutsideTheKnownResourcesIsRefusedAndNothingIsPrinted(flag, capsys):
    code = runPlan(["--lang", "java", flag, "Boleto,Nope; rm -rf"])

    captured = capsys.readouterr()
    assert code == plan.EXIT_USAGE
    assert captured.out == ""
    assert "Nope; rm -rf" in captured.err


def testAnEmptyPlanFailsBecauseAMatrixWithoutValuesFailsTheRunLater(capsys):
    code = runPlan(["--lang", "java", "--resources", "Boleto", "--exclude", "Boleto"])

    assert code == plan.EXIT_USAGE
    assert capsys.readouterr().out == ""


def testADependencyCycleIsRefused(capsys):
    code = runPlan(["--lang", "java"], order=lambda wanted: ([], ["Balance", "Boleto"]))

    captured = capsys.readouterr()
    assert code == plan.EXIT_USAGE
    assert captured.out == ""
    assert "Balance" in captured.err


def testTheOutputIsOneLineSoItFitsAGithubOutput(capsys):
    runPlan(["--lang", "java"])

    assert capsys.readouterr().out.count("\n") == 1
