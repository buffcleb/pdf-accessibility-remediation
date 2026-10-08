import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# Settings are cached on first use, so the environment must be set before app imports.
os.environ.setdefault("DATA_DIR", str(ROOT / ".pytest_data"))
os.environ["ALLOW_MOCK_PROVIDER"] = "true"
os.environ["REDIS_URL"] = "memory://"
os.environ["CELERY_RESULT_BACKEND"] = "cache+memory://"
os.environ["VERIFY_JUDGE_PROVIDER"] = "mock"  # never call a real model from tests


@pytest.fixture(scope="session")
def samples(tmp_path_factory) -> Path:
    from harness import make_samples

    out = tmp_path_factory.mktemp("samples")
    make_samples.main(str(out))
    return out


@pytest.fixture(scope="session")
def verapdf_available() -> bool:
    from app.validation.verapdf import verapdf_version

    return verapdf_version() is not None
