"""Environment-driven configuration. Nothing host-specific is hard-coded here.

Each setting's environment variable is its name upper-cased. The string under each one is its description: it's
what docs/operations/settings-reference.md shows (scripts/settings_reference.py writes it, and a test fails when a
setting has none), so write it for the person deploying the service.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", use_attribute_docstrings=True)

    # Infrastructure
    redis_url: str = "redis://localhost:6379/0"
    """Redis for the job queue. Set by the compose file or manifests."""
    celery_result_backend: str = ""
    """Where the workers keep task results; empty uses `REDIS_URL`."""
    data_dir: Path = Path("./data")
    """The shared job folder: the API and every worker must see the same one (`/data` in the image)."""
    public_base_url: str = ""
    """Prefix for download URLs in the engine's answers; empty gives relative URLs."""
    service_api_key: str = ""
    """The shared secret callers send as `X-API-Key`; the portal's `PORTAL_ENGINE_API_KEY` is the same. The API
    won't start without it (#457)."""
    allow_no_api_key: bool = False
    """`true` lets the API start without `SERVICE_API_KEY`, open to anyone who can reach it: local development and
    tests only (#457)."""
    max_upload_mb: int = 200
    """Largest document the engine accepts, in MB."""
    max_pages: int = 2000
    """A document with more pages is refused at submission (0: no limit). Keep it at or above the portal's own cap of
    2,000."""
    job_ttl_hours: int = 24
    """How long a job's folder is kept, in hours."""
    task_time_limit_s: int = 3600
    """The longest a job may run, in seconds."""
    worker_max_memory_mb: int = 1500
    """A worker process that has grown past this (MB) after a job is replaced before the next: memory a long document
    used stays with the process otherwise, and the next one starts on top of it."""
    page_workers: int = 3
    """A long document's per-page work (layout analysis, the checks' reading of each page and its artifacts) is
    shared out over this many processes, each opening the PDF itself (#124). Match the worker pod's CPU limit;
    1: one process."""
    page_workers_min_pages: int = 50
    """Documents with fewer pages aren't split over `PAGE_WORKERS` (starting the processes costs more than it
    saves)."""
    engine_version: str = ""
    """The engine's version, recorded with every result and shown in a document's Run details. Set in the image by
    `scripts/build.sh` (the image's tag, usually the commit); leave it unset."""

    # Remediation defaults
    default_language: str = "en-US"
    """The language used when neither the job nor the file says a document's language and its text is too short, or
    too mixed, to tell."""
    pdfua_part: int = 1
    """The PDF/UA part declared in XMP for PDF 1.x files. Only 1 (ISO 14289-1): the engine writes PDF 1.7 without
    PDF/UA-2's namespaces or FENote, so declaring 2 would claim a conformance the file doesn't have (#355); anything
    else is refused. A PDF 2.0 file is declared and judged as PDF/UA-2 whatever this says (#409)."""
    verapdf_path: str = "verapdf"
    """The veraPDF command line, used when the veraPDF service isn't (a name on the `PATH` or a full path)."""
    verapdf_url: str = ""
    """veraPDF's REST service, kept running (for example `http://verapdf:8080`); empty: the command line only. Set by
    the compose file or manifests."""
    verapdf_flavours: str = "ua1,ua2"
    """The veraPDF profiles checked, comma-separated. The first gives the compliance verdict; the others are for
    information."""
    verapdf_timeout_s: int = 600
    """How long one veraPDF check may take, in seconds, by the service or the command line."""
    verapdf_cli_heap: str = "1g"
    """The command line's Java heap, when the service can't be used: it runs inside the worker, beside the
    document."""
    raster_dpi: int = 200
    """Resolution of page pictures sent to the AI and of flattened pages."""
    destructive_font_path: str = ""
    """A TrueType font for the invisible text laid over flattened pages; empty finds one installed."""
    substitute_font_dir: str = ""
    """An extra folder with Liberation `*.ttf` fonts, embedded in place of the standard fonts a file uses but doesn't
    embed."""
    ocr_enabled: bool = True
    """Scanned pages are given a text layer by OCR."""
    ocr_language: str = "eng"
    """Tesseract's language code for OCR."""
    ocr_engine: str = "tesseract"
    """`tesseract` (the command line when installed: per-word confidence, needed for AI correction) or `pymupdf`
    (PyMuPDF's built-in OCR; in testing better only on barely legible scans; no AI correction)."""
    ocr_ai_enabled: bool = True
    """The AI corrects OCR words Tesseract was unsure of (needs the Tesseract command line for per-word
    confidence)."""
    ocr_ai_min_conf: float = 80.0
    """Words below this OCR confidence may be corrected by the AI."""
    ocr_ai_transcribe_below: float = 45.0
    """Lines whose mean OCR confidence is below this are read afresh by the AI."""
    ocr_ai_max_lines: int = 300
    """OCR lines the AI corrects per document, the most uncertain first."""

    # AI providers
    ai_concurrency: int = 4
    """AI calls a job makes at once."""
    ai_timeout_s: float = 180.0
    """How long one AI call may take, in seconds."""
    ai_max_retries: int = 3
    """Retries for an AI call that failed for a temporary reason (throttling, a timeout, a server error)."""
    ai_max_output_tokens: int = 800
    """The most an AI answer may be, in tokens: it caps runaway generations (small models can loop on JSON lists)."""
    allow_mock_provider: bool = False
    """`true` only for tests and the harness: the mock provider writes placeholder alt text."""
    ai_notation_enabled: bool = True
    """Specialist notation the layout rules can't read (math, chemistry, medical, engineering) goes to the AI."""
    ai_notation_max: int = 100
    """Notation blocks per document read by the AI."""
    ai_field_labels_max: int = 200
    """Form fields per document the AI is asked to name, when the page doesn't (#341)."""
    ai_images_max: int = 1000
    """Figures described per document; the rest have no alt text, and the report says so."""
    ai_tables_max: int = 100
    """Tables per document whose headers and spans the AI works out; the rest keep the layout rules'."""
    ai_order_pages_max: int = 200
    """Pages with columns given an AI reading order; the rest keep the layout rules'."""
    ai_existing_figures_max: int = 1000
    """Figure tags a document arrived with that have no description: how many the AI describes."""
    music_enabled: bool = True
    """Music examples are found by their staves and tagged, each as one figure. With the music reader (`OMR_ENABLED`)
    its alt text is a reading of the notes; otherwise the AI describes it like any picture."""
    music_max_examples: int = 80
    """Music examples (lines) read per document."""
    music_per_measure: bool = True
    """A line of music that was read becomes one figure per measure (`false`: one per line)."""
    music_max_score_lines: int = 600
    """Lines read in sheet music (most pages are music)."""
    music_whole_score: bool = True
    """Sheet music is also read as one piece, into a single MusicXML file."""
    music_whole_score_max_mb: int = 40
    """Sheet music is read as a whole piece only when its pages of music are no more than this together, in MB."""
    omr_enabled: bool = False
    """Music is read note by note by the music reader (Audiveris, asked over Redis). On a cluster, set by
    `MUSIC_READER` in `deploy.env`."""
    omr_timeout_s: int = 240
    """How long to wait for the music reader's answer, in seconds; the first one of a document includes the reader
    starting."""
    omr_page_timeout_s: int = 90
    """For a whole score: this much longer for each of its pages, in seconds."""
    formula_ocr_enabled: bool = False
    """A second reading of each formula from its picture by the formula reader (#403, `docker/formula`), to check the
    AI's against. Off unless the deployment runs it (`FORMULA_READER=on` in `deploy.env`)."""
    formula_ocr_timeout_s: int = 180
    """How long to wait for the formula reader's answer, in seconds; the first one of a document includes the reader
    starting."""
    formula_ocr_max: int = 200
    """Formulas read from their pictures in one document."""

    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    """OpenRouter's API, for calls made to it directly rather than through the gateway."""
    openrouter_api_key: str = ""
    """OpenRouter key, for direct calls and the kind-of-document classifier's Jev model."""
    openrouter_default_model: str = "openai/gpt-4o"
    """The model used for direct OpenRouter calls."""
    openrouter_table_model: str = "anthropic/claude-3.5-sonnet"
    """The model used for tables on direct OpenRouter calls."""
    classifier_jev_model: str = "jev-1.13"
    """The kind-of-document classifier's Jev model (OpenRouter's System One API, with `OPENROUTER_API_KEY`)."""

    ollama_base_url: str = "http://localhost:11434"
    """An Ollama server, for calls made to it directly rather than through the gateway (local testing)."""
    ollama_default_model: str = "qwen3-vl:8b"
    """The model used for direct Ollama calls."""
    ollama_table_model: str = ""
    """The model used for tables on direct Ollama calls; empty uses the job's model."""
    ollama_think: str = "off"
    """`off`, `on` or `model` (the model's own choice): thinking models (qwen3-vl, gemma4) are about three times
    slower with it on."""
    ollama_num_ctx: int = 0
    """Above 0, overrides the model's context window (for example 8192 for larger models)."""

    # Verification (tag checking after remediation)
    verify_enabled: bool = True
    """Every output is checked after remediation (tag checking)."""
    verify_ai_enabled: bool = True
    """An AI judge reviews figures, formulas and tables as part of the check."""
    verify_judge_provider: str = "ollama"
    """The judge's provider (`gateway`, `ollama`, `localai`, `openrouter`, or `none`). It should be a different model
    family from the one that wrote the tags."""
    verify_judge_model: str = "gemma4:26b"
    """The judge's model."""
    verify_routes: str = ""
    """Judges per kind of content, as JSON: `{"chart": {"provider": "...", "model": "..."}, "reading_order": {...}}`.
    Keys: default, chart, diagram, formula, handwriting, sheet_music, screenshot, logo, photo, illustration,
    reading_order."""
    verify_ai_reading_order: bool = False
    """The judge also reviews reading order. Off by default: in testing, local models (gemma4:26b, qwen3-vl:8b)
    misjudged correct reading orders."""
    verify_sample_rate: float = 0.10
    """Share of unflagged figures the AI judge also reviews (a job's full review reviews all)."""
    verify_ai_max_items: int = 150
    """Figures, formulas and tables the AI judge looks at per file. Raise the portal's `PORTAL_REVIEW_FINDINGS_MAX`
    with it."""
    verify_max_pages: int = 400
    """Pages given the content checks; later pages get the structure checks only."""
    verify_contrast: bool = True
    """Text below WCAG's contrast minimum is listed per page for review (#367)."""
    verify_max_output_tokens: int = 1500
    """The most a judge's answer may be, in tokens: it explains problems and suggests alt text, so it gets more room
    than a writer."""

    localai_base_url: str = "http://localhost:8080/v1"
    """A LocalAI server (or any OpenAI-compatible one), for calls made to it directly rather than through the
    gateway (local testing)."""
    localai_api_key: str = ""
    """LocalAI's key, when it has one."""
    localai_default_model: str = "qwen3-vl-8b-instruct"
    """The model used for direct LocalAI calls."""
    localai_reasoning_effort: str = "none"
    """Thinking models (gemma 4, qwen3) spend the output budget reasoning and return empty answers; `none` turns that
    off on LocalAI. Empty leaves the field out."""

    # AI gateway (LiteLLM). Every model the service offers is a LiteLLM model name; the portal's AI tiers
    # pick one per job.
    gateway_base_url: str = "http://litellm:4000/v1"
    """The AI gateway (LiteLLM). Set by the compose file or manifests."""
    gateway_api_key: str = ""
    """The engine's key to the gateway: a LiteLLM virtual key (or the master key in local testing). On a cluster,
    `deploy.sh` makes the engine a key of its own (#457)."""
    gateway_default_model: str = "local-vision"
    """The gateway model used when a job doesn't name one."""
    gateway_table_model: str = ""
    """The gateway model used for tables; empty uses the job's model."""
    gateway_json_mode: bool = False
    """Ask the gateway for JSON output. Off: local backends vary, so answers are parsed leniently instead."""
    gateway_reasoning_effort: str = "none"
    """Passed through to backends that think (LocalAI); empty leaves it out."""
    ai_gateway_only: bool = False
    """Every AI call goes through the gateway (#361). A provider named directly (localai, ollama, openrouter: the
    portal's fallback, the default judge) becomes the gateway's default model, or its judge model, and the Jev
    classifier goes through the gateway's `/systemone` route. The Kubernetes manifests set it to `true`: there the
    engine has no internet access."""
    gateway_judge_model: str = "local-judge"
    """The gateway model a directly named judge becomes, with `AI_GATEWAY_ONLY`."""

    # Adobe PDF Services (Autotag). Every limit is configurable because Adobe doesn't publish them; set them from
    # the contract.
    adobe_enabled: bool = False
    """Adobe Autotag can be used. Off unless switched on; documents sent there leave this service."""
    adobe_client_id: str = ""
    """Adobe PDF Services client ID."""
    adobe_client_secret: str = ""
    """Adobe PDF Services client secret."""
    adobe_base_url: str = "https://pdf-services-ue1.adobe.io"
    """Adobe PDF Services' address (`https://pdf-services-ew1.adobe.io` in the EU, or the fake Adobe's for
    testing)."""
    adobe_rpm: int = 25
    """API requests per minute across all workers (token, upload, start, status, delete)."""
    adobe_max_concurrent: int = 2
    """Documents (or parts) at Adobe at once across all workers."""
    adobe_max_retries: int = 4
    """Retries for throttling (429 "too many requests") and server errors; running out of transactions isn't
    retried."""
    adobe_max_pages_per_call: int = 100
    """Larger documents are split, tagged in parts and merged."""
    adobe_max_mb_per_call: int = 100
    """Larger documents are split, by size in MB."""
    adobe_poll_seconds: float = 3.0
    """How often to ask Adobe whether a part is done, in seconds."""
    adobe_timeout_minutes: int = 20
    """Per part, from upload to download, in minutes."""
    adobe_generate_report: bool = True
    """Adobe's own accessibility report (xlsx) is kept with the job."""
    adobe_shift_headings: bool = False
    """Passed to Autotag as `shiftHeadings`: Adobe moves the headings it finds down a level."""

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def flavours(self) -> list[str]:
        return [f.strip() for f in self.verapdf_flavours.split(",") if f.strip()]


    @field_validator("pdfua_part")
    @classmethod
    def _pdfua_part(cls, v: int) -> int:
        if v != 1:
            raise ValueError("PDFUA_PART must be 1: it's the part for PDF 1.x files, which can only be PDF/UA-1. A PDF "
                             "2.0 file is labelled and checked as PDF/UA-2 whatever this says (#409)")
        return v

@lru_cache
def get_settings() -> Settings:
    return Settings()
