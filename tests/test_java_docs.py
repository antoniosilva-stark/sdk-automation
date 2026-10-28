import re
import yaml
import pytest

from conftest import REPO_ROOT

SCHEMAS = REPO_ROOT / "apis/schemas"
ALL_FLAGS = {"x-sdk-create", "x-sdk-get", "x-sdk-query", "x-sdk-page", "x-sdk-log", "x-sdk-delete", "x-sdk-put",
             "x-sdk-update", "x-sdk-cancel", "x-sdk-pdf"}
RESIDUE = ("datetime.", '\\"', "None", "User.defaultUser", "starkbank.user ")


def _schemas(fileName: str) -> dict:
    return yaml.safe_load((SCHEMAS / f"{fileName}.yaml").read_text(encoding="utf-8"))["components"]["schemas"]


def _rendered(javaDocs, resource: str, fileName: str, flags: set = ALL_FLAGS) -> dict:
    schemas = _schemas(fileName)
    entity = javaDocs.describeEntity(schemas[resource], schemas.get(f"{resource}Create"))
    return javaDocs.renderDocs(resource, schemas[resource].get("x-sdk-docs") or {}, flags, entity)


def test_defaultNoneBecomesDefaultNull(javaDocs):
    assert javaDocs.mapType("list of strings, default None") == "list of strings, default null"
    assert javaDocs.mapType("integer, default 100") == "integer, default 100"
    assert javaDocs.mapType('string, default "-created"') == 'string, default "-created"'


def test_pythonDateTypesBecomeString(javaDocs):
    assert javaDocs.mapType("datetime.date or string, default None") == "string, default null"
    assert javaDocs.mapType("datetime.datetime or string, default None") == "string, default null"
    assert javaDocs.mapType("datetime.datetime") == "string"
    assert javaDocs.mapType("datetime.date") == "string"


def test_unlimitedIfNoneBecomesUnlimitedIfNull(javaDocs):
    text = "maximum number of objects to be retrieved. Unlimited if None. ex: 35"

    assert javaDocs.mapText(text, "Transfer") == "maximum number of objects to be retrieved. Unlimited if null. ex: 35"


def test_dateCallBecomesAnIsoString(javaDocs):
    text = "date filter for objects created only after specified date. ex: datetime.date(2020, 3, 10)"

    assert javaDocs.mapText(text, "Transfer").endswith('ex: "2020-03-10"')


def test_datetimeCallBecomesAnIsoStringWithOffset(javaDocs):
    text = "creation datetime for the transfer. ex: datetime.datetime(2020, 3, 10, 10, 30, 0, 0)"

    assert javaDocs.mapText(text, "Transfer").endswith('ex: "2020-03-10 10:30:00.000000+00:00"')


def test_escapedQuotesBecomePlainQuotes(javaDocs):
    assert javaDocs.mapText('ex: \\"5656565656565656\\"', "Transfer") == 'ex: "5656565656565656"'


@pytest.mark.parametrize("reference", ["starkbank.user", "starkbank.User.defaultUser", "starkbank.Settings.user"])
def test_everyFormOfTheUserReferenceBecomesSettingsUser(javaDocs, reference):
    text = f"Not necessary if {reference} was set before function call"

    assert javaDocs.mapText(text, "Transfer") == "Not necessary if starkbank.Settings.user was set before function call"


def test_userLineIsTheTextTheMaintainerDecided(javaDocs):
    lines = _rendered(javaDocs, "SplitProfile", "splitprofile")["lines"]["getUser"]

    assert ("@param user [Organization/Project object, default null]: Organization or Project object."
            " Not necessary if starkbank.Settings.user was set before function call") in lines


def test_userLineIsTheSameWhateverThePythonSaid(javaDocs):
    docs = {"get": {"summary": "x", "description": [], "returns": ["x"], "params": [
        {"name": "user", "type": "Project object", "description": "Project object. starkbank.User.defaultUser",
         "section": "optional"}]}}
    entity = {"description": "", "fields": []}

    lines = javaDocs.renderDocs("Transfer", docs, {"x-sdk-get"}, entity)["lines"]["getUser"]

    assert [line for line in lines if line.startswith("@param user")] == [
        "@param user [Organization/Project object, default null]: Organization or Project object."
        " Not necessary if starkbank.Settings.user was set before function call"]


def test_lowerCamelClassNameBecomesTheClassName(javaDocs):
    text = "unique id returned when the splitProfile is created."

    assert javaDocs.mapText(text, "SplitProfile") == "unique id returned when the SplitProfile is created."


def test_singleWordClassNameIsLeftAloneBecauseItIsAnEnglishWord(javaDocs):
    assert javaDocs.mapText("frequency of transfer", "Transfer") == "frequency of transfer"


def test_logModuleReferenceBecomesTheClassNameFollowedByLog(javaDocs):
    assert javaDocs.mapText("splitprofile.Log object", "SplitProfile") == "SplitProfile Log object"
    assert javaDocs.mapText("SplitProfile.Log object", "SplitProfile") == "SplitProfile Log object"


def test_aCommentTerminatorCannotCloseTheJavadoc(javaDocs):
    assert "*/" not in javaDocs.mapText("pattern a*/b and \\u0041", "Transfer")


@pytest.mark.parametrize("operation,expected", [
    ("getUser", "Retrieve a specific SplitProfile"),
    ("queryUser", "Retrieve SplitProfiles"),
    ("pageUser", "Retrieve paged SplitProfiles"),
    ("putUser", "Create or update a SplitProfile"),
    ("logGetUser", "Retrieve a specific SplitProfile Log"),
    ("logQueryUser", "Retrieve SplitProfile Logs"),
    ("logPageUser", "Retrieve paged SplitProfile.Logs"),
])
def test_titleFollowsTheVerbPhrase(javaDocs, operation, expected):
    assert _rendered(javaDocs, "SplitProfile", "splitprofile")["lines"][operation][0] == expected


def test_createAndDeleteTitlesUseTheVerbPhrase(javaDocs):
    lines = _rendered(javaDocs, "Webhook", "webhook")["lines"]

    assert lines["createPlain"][0] == "Create Webhooks"
    assert lines["deletePlain"][0] == "Delete a Webhook entity"


def test_deleteTitleAgreesTheArticleWithTheClassName(javaDocs):
    docs = {"delete": {"summary": "x", "description": [], "params": [], "returns": ["deleted Invoice object"]}}
    entity = {"description": "", "fields": []}

    lines = javaDocs.renderDocs("Invoice", docs, {"x-sdk-delete"}, entity)["lines"]

    assert lines["deletePlain"][0] == "Delete an Invoice entity"


def test_updateCancelAndPdfTitlesComeFromThePythonSummary(javaDocs):
    lines = _rendered(javaDocs, "Invoice", "invoice")["lines"]

    assert lines["updatePlain"][0] == "Update Invoice entity"
    assert lines["pdfPlain"][0] == "Retrieve a specific Invoice pdf file"


def test_pluralEndingInYBecomesIes(javaDocs):
    assert javaDocs.pluralize("MerchantCategory") == "MerchantCategories"
    assert javaDocs.pluralize("DictKey") == "DictKeys"


def test_irregularPluralIsWarnedAndNotGuessed(javaDocs):
    result = _rendered(javaDocs, "BoletoHolmes", "boletoholmes", {"x-sdk-query"})

    assert javaDocs.pluralize("BoletoHolmes") is None
    assert any("BoletoHolmes" in warning for warning in result["warnings"])
    assert "Holmeses" not in " ".join(result["lines"]["queryPlain"])
    assert "BoletoHolmess" not in result["lines"]["queryPlain"][0]


def test_queryParamsLineAndFiltersFollowTheDominantForm(javaDocs):
    lines = _rendered(javaDocs, "SplitProfile", "splitprofile")["lines"]["queryParamsUser"]

    assert "@param params map of parameters for the query" in lines
    assert "limit [integer, default null]: maximum number of objects to be retrieved. Unlimited if null. ex: 35" in lines
    assert ('after [string, default null] date filter for objects created or updated only after specified date.'
            ' ex: "2020-03-10"') in lines
    assert lines.index("@param params map of parameters for the query") < lines.index(
        next(line for line in lines if line.startswith("@param user")))


def test_pageParamsLineIsTheDominantOneAndKeepsCursorAndStatusFilters(javaDocs):
    lines = _rendered(javaDocs, "Transfer", "transfer")["lines"]["pageParams"]

    assert "@param params parameters of the query" in lines
    assert "cursor [string, default null]: cursor returned on the previous page function call" in lines
    assert any(line.startswith("status [string, default null]: filter for status") for line in lines)
    assert not any(line.startswith("@param user") for line in lines)


def test_overloadWithoutArgumentsHasNoParametersSection(javaDocs):
    lines = _rendered(javaDocs, "SplitProfile", "splitprofile")["lines"]

    assert "Parameters:" not in lines["queryPlain"]
    assert "Parameters:" not in lines["pagePlain"]
    assert "Parameters:" in lines["getPlain"]


def test_eachOverloadListsOnlyItsOwnParameters(javaDocs):
    lines = _rendered(javaDocs, "SplitProfile", "splitprofile")["lines"]

    assert not any(line.startswith("@param user") for line in lines["queryParams"])
    assert not any(line.startswith("@param params") for line in lines["queryUser"])
    assert any(line.startswith("@param user") for line in lines["queryParamsUser"])


def test_returnIsTheItemTextFollowedByTheThrowsLine(javaDocs):
    lines = _rendered(javaDocs, "SplitProfile", "splitprofile")["lines"]["getPlain"]

    assert lines[-3:] == ["Return:", "@return SplitProfile object with updated attributes",
                          "@throws Exception error in the request"]


def test_pageReturnNamesThePageFields(javaDocs):
    lines = _rendered(javaDocs, "SplitProfile", "splitprofile")["lines"]

    assert lines["pagePlain"][-5:] == [
        "Return:",
        "@return SplitProfile.Page object:",
        "SplitProfile.Page.splitProfiles: list of SplitProfile objects with updated attributes",
        "SplitProfile.Page.cursor: cursor to retrieve the next page of SplitProfile objects",
        "@throws Exception error in the request",
    ]
    assert "@return SplitProfile.Log.Page object:" in lines["logPagePlain"]
    assert ("SplitProfile.Log.Page.logs: list of SplitProfile Log objects with updated attributes"
            in lines["logPagePlain"])


def test_dataDocHasOneParametersTitleAndOneLinePerField(javaDocs):
    lines = _rendered(javaDocs, "SplitProfile", "splitprofile")["lines"]["dataDoc"]

    assert lines[0] == "SplitProfile object"
    assert lines[1] == "<p>"
    assert lines.count("Parameters:") == 1
    assert 'created [string]: creation datetime for the SplitProfile. ex: "2020-03-10 10:30:00.000000+00:00"' in lines
    assert "interval [string]: frequency of transfer, default \"week\". Options: \"day\", \"week\", \"month\"" in lines


def test_arrayFieldKeepsItsDescription(javaDocs):
    lines = _rendered(javaDocs, "SplitProfile", "splitprofile")["lines"]["dataDoc"]

    assert "tags [list of strings]: list of strings for tagging" in lines


def test_constructorMapDocSplitsCreatableFromReturnOnly(javaDocs):
    lines = _rendered(javaDocs, "SplitProfile", "splitprofile")["lines"]["ctorMapDoc"]

    returnOnly = lines[lines.index("Attributes (return-only):"):]
    assert "@param data map of properties for the creation of the SplitProfile" in lines
    assert any(line.startswith("interval [string]") for line in lines[:lines.index("Attributes (return-only):")])
    assert [line.split(" ")[0] for line in returnOnly[1:-1]] == ["status", "created", "updated"]
    assert returnOnly[-1] == "@throws Exception error in the request"


def test_constructorAllArgsDocHasOneParamTagPerField(javaDocs):
    lines = _rendered(javaDocs, "SplitProfile", "splitprofile")["lines"]["ctorAllDoc"]

    assert [line.split(" ")[1] for line in lines if line.startswith("@param")] == [
        "interval", "delay", "tags", "id", "status", "created", "updated"]


def test_createListParameterIsDocumentedEvenWhenPythonTakesTheFieldsApart(javaDocs):
    lines = _rendered(javaDocs, "Webhook", "webhook")["lines"]["createPlain"]

    assert "@param webhooks [list of Webhook objects or HashMaps]: list of Webhook objects to be created in the API" in lines
    assert not any(line.startswith(("@param url", "@param subscriptions")) for line in lines)


def test_createDocFromPythonKeepsItsListDescription(javaDocs):
    lines = _rendered(javaDocs, "Transfer", "transfer")["lines"]["createPlain"]

    assert "@param transfers [list of Transfer objects or HashMaps]: list of Transfer objects to be created in the API" in lines


def test_updateDocListsIdThePatchMapAndItsFields(javaDocs):
    lines = _rendered(javaDocs, "Invoice", "invoice")["lines"]["updateUser"]

    assert "@param patchData map of parameters to patch" in lines
    assert lines.index("@param patchData map of parameters to patch") < lines.index(
        next(line for line in lines if line.startswith("amount [")))
    assert any(line.startswith("due [string, default now + 2 days]") for line in lines)


def test_operationWithoutPythonDocsFallsBackToTheGenericBlockAndIsCounted(javaDocs):
    entity = {"description": "", "fields": []}

    result = javaDocs.renderDocs("Widget", {}, {"x-sdk-get", "x-sdk-query"}, entity)

    assert sorted(result["fallbacks"]) == ["get", "query"]
    assert result["lines"]["getPlain"][0] == "Retrieve a specific Widget"
    assert "@return Widget object with updated attributes" in result["lines"]["getPlain"]


@pytest.mark.parametrize("resource,fileName", [
    ("SplitProfile", "splitprofile"), ("Transaction", "transaction"), ("Transfer", "transfer"),
    ("Webhook", "webhook"),
])
def test_noGoldenResourceCarriesPythonResidueOrAnEmptyDescription(javaDocs, resource, fileName):
    result = _rendered(javaDocs, resource, fileName)

    for key, lines in result["lines"].items():
        for line in lines:
            assert not any(token in line for token in RESIDUE), f"{resource}.{key}: {line}"
            assert not re.search(r"\]:\s*$", line), f"{resource}.{key}: descricao vazia em {line}"
            assert line == line.rstrip(), f"{resource}.{key}: espaco no fim de {line!r}"
            assert line.strip() not in ("", "*"), f"{resource}.{key}: linha vazia"
