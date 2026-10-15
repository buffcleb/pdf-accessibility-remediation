"""The reviewer is told how this engine words a formula. It reported readings such as "H 3 O plus" as incomplete
for not saying "subscript": the style the engine itself writes, flagged as a fault on every chemistry document."""
import asyncio

from app.ai import prompts
from app.ai.mock import MockProvider


def _asked(kind):
    seen = {}

    async def go():
        async with MockProvider() as ai:
            async def fake(prompt, images, **kw):
                seen["prompt"] = prompt
                return {"verdict": "accurate", "problems": [], "hallucinated_details": [], "suggested_alt": "", "confidence": 0.9}
            ai.chat_json = fake
            return await ai.judge_alt(b"", kind, "H 3 O plus", "")

    asyncio.run(go())
    return seen["prompt"]


def test_a_formulas_reviewer_is_told_the_reading_style():
    prompt = _asked("formula")
    assert "'H 3 O plus'" in prompt and "'H 2 O'" in prompt            # counts and charges as the engine says them
    assert "'sub'" in prompt and "'to the power of'" in prompt          # and math scripts
    assert "never because the word 'subscript'" in prompt
    assert "Every symbol, subscript and operator matters" in prompt     # what it must still check is unchanged


def test_other_kinds_of_picture_are_reviewed_as_before():
    for kind in ("chart", "diagram", "photo"):
        assert "H 3 O plus'" not in _asked(kind).replace('"""H 3 O plus"""', "")
    assert prompts.ALT_CHECKLISTS["chart"].startswith("For a chart or graph")


def test_the_style_named_is_the_one_the_engine_writes():
    """If the chemistry style changes, the reviewer's note must change with it."""
    from app.pipeline.notation import say_chemistry

    assert say_chemistry("H3O plus") == "H 3 O plus" and say_chemistry("H2O") == "H 2 O" and say_chemistry("CO2") == "C O 2"
