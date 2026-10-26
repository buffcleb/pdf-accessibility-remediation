"""The "volume nearly full" alert's threshold comes from deploy.env (VOLUME_ALERT_FREE_PERCENT): scripts/deploy.sh puts
it into the rules Prometheus loads. Rendered in a copy of the deploy files, as the ingress tests do."""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.engine.test_ingress_controllers import ROOT, _render

pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("perl")), reason="needs bash and perl")


def _volume_rule(rules_file: Path) -> str:
    rules = yaml.safe_load(rules_file.read_text())
    rule = next(r for g in rules["groups"] for r in g["rules"] if r.get("alert") == "VolumeFilling")
    return rule["expr"]


def _rendered_rules(tmp_path: Path, **extra: str) -> Path:
    _render(tmp_path, "nginx", "cluster", pattern="95-*.yaml", **extra)
    return max((tmp_path).glob("*/deploy/k8s/.rendered/rules.yml"), key=lambda p: p.stat().st_mtime)


def test_the_default_is_ten_percent(tmp_path):
    assert "< 0.1 " in _volume_rule(_rendered_rules(tmp_path))
    # The rules in the repository say the same, so promtool's tests of them still hold
    assert "< 0.1 " in _volume_rule(ROOT / "deploy/k8s/monitoring/rules.yml")


def test_a_campus_sets_twenty(tmp_path):
    expr = _volume_rule(_rendered_rules(tmp_path, VOLUME_ALERT_FREE_PERCENT="20"))
    assert "< 0.2 " in expr and "< 0.1 " not in expr


@pytest.mark.parametrize("bad", ["0", "95", "15%", "ten"])
def test_a_setting_out_of_range_stops_the_deploy(tmp_path, bad):
    with pytest.raises(AssertionError, match="VOLUME_ALERT_FREE_PERCENT"):
        _render(tmp_path, "nginx", "cluster", pattern="95-*.yaml", VOLUME_ALERT_FREE_PERCENT=bad)
