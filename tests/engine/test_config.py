

def test_only_pdfua_1_can_be_declared(monkeypatch):
    """#355: the engine writes PDF/UA-1. Declaring part 2 would claim PDF 2.0, its namespaces and FENote, which it
    doesn't write, so the setting refuses it at start-up."""
    import pytest
    from pydantic import ValidationError

    from app.config import Settings

    monkeypatch.setenv("PDFUA_PART", "2")
    with pytest.raises(ValidationError, match="PDFUA_PART must be 1"):
        Settings()
    monkeypatch.setenv("PDFUA_PART", "1")
    assert Settings().pdfua_part == 1
