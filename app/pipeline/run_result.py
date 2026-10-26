"""What a job returns and writes as its JSON report, the same four parts for every kind of job (remediation, report
only, Adobe, tag edits), and the metrics more than one place fills (refactor plan item 7). The report readers
(app/reports, the portal) read these keys.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class RunResult:
    files: dict
    metrics: dict
    reports: dict
    warnings: list

    def to_dict(self) -> dict:
        """The result as the report has it (the same dictionaries, not copies)."""
        return {"files": self.files, "metrics": self.metrics, "reports": self.reports, "warnings": self.warnings}


def ai_usage(ai) -> dict:
    """The AI's tokens, cost, calls and failures so far, as a run's metrics have them (nothing used without one)."""
    return {
        "ai_tokens_used": {"prompt_tokens": ai.usage.prompt_tokens if ai else 0,
                           "completion_tokens": ai.usage.completion_tokens if ai else 0},
        "ai_cost_usd": round(ai.usage.cost_usd, 6) if ai else 0.0,
        "ai_calls": ai.calls if ai else 0,
        "ai_failures": ai.failures if ai else 0,
    }


def timed(metrics: dict, started: float, clock) -> None:
    """The run's time so far, in all and by stage (clock: the run's StageClock)."""
    metrics["processing_time_seconds"] = round(time.monotonic() - started, 2)
    metrics["stage_seconds"] = clock.seconds()
