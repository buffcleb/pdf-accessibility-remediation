"""config/litellm.yaml as shipped: the names AI levels point at. A level stores its model by name, so a name that
is repeated, or one the documentation gives that the file doesn't have, leaves a level without a model."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = yaml.safe_load((ROOT / "config" / "litellm.yaml").read_text())
MODELS = {m["model_name"]: m["litellm_params"] for m in CONFIG["model_list"]}


CREDENTIALS = ("api_key", "aws_access_key_id", "aws_secret_access_key")


def test_each_name_is_listed_once_and_no_key_is_written_in_the_file():
    names = [m["model_name"] for m in CONFIG["model_list"]]
    assert sorted(names) == sorted(set(names))
    for name, params in MODELS.items():
        given = [k for k in CREDENTIALS if k in params]
        assert given, name  # every model says where its key comes from
        assert all(str(params[k]).startswith("os.environ/") for k in given), name


def test_the_writers_and_reviewers_the_documentation_names_are_served():
    doc = (ROOT / "docs" / "operations" / "ai-models.md").read_text()
    named = set(re.findall(r"`((?:local|open|cloud|large|commercial)[a-z-]*-(?:vision|judge))`", doc))
    assert {"open-small-vision", "open-large-vision", "commercial-small-vision", "commercial-medium-vision",
            "commercial-large-vision", "open-judge", "commercial-judge"} <= named
    assert named <= set(MODELS)


def test_a_name_says_family_size_and_role_and_never_the_model():
    """"large-vision" beside "commercial-large-vision" didn't say which family it was, and "cloud" told nothing
    apart once every paid model was hosted."""
    for name in MODELS:
        assert re.fullmatch(r"local-(vision|judge)|(open|commercial)-(small|medium|large)-vision|(open|commercial)-judge"
                            r"|trial-[a-z]-vision", name), name


def test_a_writer_and_the_reviewer_suggested_for_it_are_from_different_families():
    def family(name):
        model = MODELS[name]["model"]
        if model.startswith("bedrock/"):  # bedrock/converse/us.amazon.nova-lite-v1:0: the vendor after the region
            return model.rsplit("/", 1)[1].split(".")[1]
        return model.removeprefix("openrouter/").split("/")[0]

    for writer, judge in (("open-small-vision", "open-judge"), ("open-large-vision", "open-judge"),
                          ("commercial-small-vision", "open-judge"), ("commercial-medium-vision", "commercial-judge"),
                          ("commercial-large-vision", "commercial-judge"), ("trial-a-vision", "open-judge")):
        assert family(writer) != family(judge), (writer, judge)


def test_the_jev_route_adds_the_openrouter_key_from_the_environment():
    """#361: the engine reaches Jev only through the gateway, which holds the key; the file never does."""
    (route,) = CONFIG["general_settings"]["pass_through_endpoints"]
    assert route["path"] == "/systemone" and route["target"] == "https://openrouter.ai/api/v1/systemone"
    assert route["headers"]["Authorization"] == "Bearer os.environ/OPENROUTER_API_KEY"
    assert route["forward_headers"] is False  # the engine's gateway key isn't passed on to OpenRouter


def test_bedrock_models_stay_in_us_regions_and_say_their_price():
    """A "us." profile keeps a request in US regions; a "global." one could run it anywhere. And the portal's cost
    for a job comes from the gateway, which may not know a model's Bedrock price."""
    bedrock = {n: p for n, p in MODELS.items() if p["model"].startswith("bedrock/")}
    assert bedrock
    info = {m["model_name"]: m.get("model_info", {}) for m in CONFIG["model_list"]}
    for name, params in bedrock.items():
        assert params["model"].rsplit("/", 1)[1].startswith("us."), name
        assert params["aws_region_name"] == "os.environ/AWS_REGION_NAME", name
        assert info[name].get("input_cost_per_token", 0) > 0 and info[name].get("output_cost_per_token", 0) > 0, name


def test_aws_keys_go_to_the_gateway_only():
    """deploy.sh splits engine.env into the engine's and the gateway's secrets: AWS keys are the gateway's alone."""
    script = (ROOT / "scripts" / "deploy.sh").read_text()
    gateway_only = re.search(r"^GATEWAY_ONLY='(.*)'$", script, re.M).group(1)
    assert re.match(gateway_only, "AWS_SECRET_ACCESS_KEY") and re.match(gateway_only, "AWS_ACCESS_KEY_ID")
