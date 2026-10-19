"""config/litellm.yaml as shipped: the names AI levels point at. A level stores its model by name, so a name that
is repeated, or one the documentation gives that the file doesn't have, leaves a level without a model."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = yaml.safe_load((ROOT / "config" / "litellm.yaml").read_text())
MODELS = {m["model_name"]: m["litellm_params"] for m in CONFIG["model_list"]}


def test_each_name_is_listed_once_and_no_key_is_written_in_the_file():
    names = [m["model_name"] for m in CONFIG["model_list"]]
    assert sorted(names) == sorted(set(names))
    for name, params in MODELS.items():
        assert params["api_key"].startswith("os.environ/"), name


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
        assert re.fullmatch(r"local-(vision|judge)|(open|commercial)-(small|medium|large)-vision|(open|commercial)-judge",
                            name), name


def test_a_writer_and_the_reviewer_suggested_for_it_are_from_different_families():
    def family(name):
        return MODELS[name]["model"].removeprefix("openrouter/").split("/")[0]

    for writer, judge in (("open-small-vision", "open-judge"), ("open-large-vision", "open-judge"),
                          ("commercial-small-vision", "open-judge"), ("commercial-medium-vision", "commercial-judge"),
                          ("commercial-large-vision", "commercial-judge")):
        assert family(writer) != family(judge), (writer, judge)


def test_the_jev_route_adds_the_openrouter_key_from_the_environment():
    """#361: the engine reaches Jev only through the gateway, which holds the key; the file never does."""
    (route,) = CONFIG["general_settings"]["pass_through_endpoints"]
    assert route["path"] == "/systemone" and route["target"] == "https://openrouter.ai/api/v1/systemone"
    assert route["headers"]["Authorization"] == "Bearer os.environ/OPENROUTER_API_KEY"
    assert route["forward_headers"] is False  # the engine's gateway key isn't passed on to OpenRouter
