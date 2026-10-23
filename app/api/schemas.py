from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RemediationFlags(BaseModel):
    debug: bool = False
    report_only: bool = False
    return_base64: bool = False
    language: str | None = Field(default=None, description="BCP-47 document language, e.g. en-US")
    title: str | None = Field(default=None, description="Override the document title")
    full_review: bool = Field(default=False, description="AI-review every figure and page, not just flagged ones + a sample")
    verify_ai: bool = Field(default=True, description="Run the AI review layer of tag verification")
    notation_ai: bool = Field(default=True, description="Let the AI read specialist notation the layout rules can't")
    ocr_ai: bool = Field(default=True, description="Let the AI correct OCR words Tesseract was unsure of")
    ignore_existing: bool = Field(default=False, description="Ignore the document's own tags (poor ones) and tag it "
                                  "afresh from the page")
    validator: Literal["service", "command_line"] | None = Field(
        default=None, description="veraPDF's running service or its command line; default: the service when configured")
    verapdf_ua2: bool = Field(default=True, description="Also check PDF/UA-2 (shown for information)")
    report_branding: dict[str, str] | None = Field(
        default=None, description="For the PDF report: service_name, organization, accent_color (#rrggbb), ai_label")


class SubmitResponse(BaseModel):
    job_id: str
    status: Literal["processing"] = "processing"


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class Metrics(BaseModel):
    model_config = ConfigDict(extra="allow")

    pages_processed: int = 0
    ai_tokens_used: TokenUsage = TokenUsage()
    processing_time_seconds: float = 0.0
    ai_calls: int = 0


class Files(BaseModel):
    remediated_primary: str | None = None
    remediated_visual_fallback: str | None = None
    enhanced: str | None = None  # Adobe route with AI: Adobe's file plus AI alt text (a separate copy)


class Reports(BaseModel):
    verapdf_compliance_report: str | None = None
    verapdf_before: str | None = None
    verapdf_after: dict[str, str | None] | None = None
    comparison: dict[str, Any] | None = None
    remediation_summary: str = ""
    ai_findings: list[str] | None = None
    full_report: str | None = None  # JSON with per-element tagging details
    markdown_report: str | None = None  # same report rendered as Markdown
    report_pdf: str | None = None  # same report as an accessible PDF/UA-1 PDF, for people
    tag_verification: dict[str, Any] | None = None
    tag_maps: dict[str, str | None] | None = None  # original / primary / visual_fallback, for the tag manager
    music_files: str | None = None  # a zip of MusicXML: the notation of each music example that was read
    music_score: str | None = None  # sheet music: the whole piece as one MusicXML file
    math_files: str | None = None  # a zip of MathML: each formula the AI read, and the formula list (#403)
    math_list: str | None = None  # the formula list: an HTML page with every formula in MathML
    math_nemeth: str | None = None  # every formula in Nemeth braille (.brf), from MathCAT
    math_ueb: str | None = None  # every formula in UEB math braille (.brf), from MathCAT
    edits_applied: list[str] | None = None  # edit jobs: one line per tag edit
    adobe_reports: list[str] | None = None  # Adobe's own accessibility report(s) (xlsx), Adobe route only


class JobResponse(BaseModel):
    job_id: str
    status: Literal["processing", "completed", "failed"]
    progress: dict[str, Any] | None = None
    # While an edit job is still checking: its file and tag map, already final (#428)
    partial: dict[str, Any] | None = None
    files: Files | None = None
    metrics: Metrics | None = None
    reports: Reports | None = None
    warnings: list[str] | None = None
    error: dict[str, Any] | None = None
