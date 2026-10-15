"""Environment-driven configuration. Nothing host-specific is hard-coded here."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Infrastructure
    redis_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = ""  # empty -> redis_url
    data_dir: Path = Path("./data")
    public_base_url: str = ""  # prefix for download URLs; empty -> relative URLs
    service_api_key: str = ""  # optional shared secret with the upstream service (X-API-Key)
    max_upload_mb: int = 200
    max_pages: int = 2000  # a document with more pages is refused at submission (0: no limit); the portal's own cap is 2,000
    job_ttl_hours: int = 24
    task_time_limit_s: int = 3600

    # Remediation defaults
    default_language: str = "en-US"
    pdfua_part: int = 1  # PDF/UA part declared in XMP (1 = ISO 14289-1)
    verapdf_path: str = "verapdf"
    verapdf_url: str = ""  # veraPDF's REST service (kept running), e.g. http://verapdf:8080; empty: command line only
    verapdf_flavours: str = "ua1,ua2"  # first flavour is the primary compliance verdict; others are informational
    verapdf_timeout_s: int = 600
    raster_dpi: int = 200  # used for AI page renders and destructive flattening
    destructive_font_path: str = ""  # TTF for invisible text in flattened pages; empty -> autodetect
    substitute_font_dir: str = ""  # extra dir with Liberation*.ttf for embedding standard-font substitutes
    ocr_enabled: bool = True
    ocr_language: str = "eng"
    # OCR engine: "tesseract" (the command line when installed: per-word confidence, needed for AI correction)
    # or "pymupdf" (PyMuPDF's built-in OCR; in testing better only on barely legible scans; no AI correction)
    ocr_engine: str = "tesseract"
    # AI-assisted OCR correction (needs the Tesseract command line for per-word confidence)
    ocr_ai_enabled: bool = True
    ocr_ai_min_conf: float = 80.0  # words below this confidence may be corrected by the AI
    ocr_ai_transcribe_below: float = 45.0  # lines whose mean confidence is below this are read afresh by the AI
    ocr_ai_max_lines: int = 300  # per document; the most uncertain first

    # AI providers
    ai_concurrency: int = 4
    ai_timeout_s: float = 180.0
    ai_max_retries: int = 3
    ai_max_output_tokens: int = 800  # caps runaway generations (small models can loop on JSON lists)
    allow_mock_provider: bool = False
    # Specialist notation the layout rules can't read (math, chemistry, medical, engineering) goes to the AI
    ai_notation_enabled: bool = True
    ai_notation_max: int = 100  # blocks per document
    ai_images_max: int = 1000  # figures described per document; the rest have no alt text, and the report says so
    ai_tables_max: int = 100  # tables whose headers and spans the AI works out; the rest keep the layout rules'
    ai_order_pages_max: int = 200  # pages with columns given an AI reading order; the rest keep the layout rules'
    # Figure tags a document arrived with that have no description: how many the AI describes
    ai_existing_figures_max: int = 1000
    # Music notation: each example becomes one figure; with the reading service (Audiveris, asked over Redis) its
    # alt text is a reading of the notes, otherwise the AI describes it like any picture
    music_enabled: bool = True
    music_max_examples: int = 80  # per document
    music_per_measure: bool = True  # a line of music that was read becomes one figure per measure
    # Sheet music (most pages are music): more lines are read, and the whole piece is also read as one MusicXML file
    music_max_score_lines: int = 600
    music_whole_score: bool = True
    music_whole_score_max_mb: int = 40
    omr_enabled: bool = False
    omr_timeout_s: int = 240  # wait for an answer; the first one of a document includes the service starting
    omr_page_timeout_s: int = 90  # a whole score: this much longer for each of its pages

    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str = ""
    openrouter_default_model: str = "openai/gpt-4o"
    openrouter_table_model: str = "anthropic/claude-3.5-sonnet"
    # The kind-of-document classifier's Jev model (OpenRouter's System One API, with the key above)
    classifier_jev_model: str = "jev-1.13"

    ollama_base_url: str = "http://localhost:11434"
    ollama_default_model: str = "qwen3-vl:8b"
    ollama_table_model: str = ""  # empty -> use the request model
    ollama_think: str = "off"  # off | on | model: thinking models (qwen3-vl, gemma4) are ~3x slower with it on
    ollama_num_ctx: int = 0  # >0 overrides the model's context window (e.g. 8192 for larger models)

    # Verification (tag checking after remediation)
    verify_enabled: bool = True
    verify_ai_enabled: bool = True
    verify_judge_provider: str = "ollama"  # should be a different model family from the generator
    verify_judge_model: str = "gemma4:26b"
    verify_routes: str = ""  # JSON: {"chart": {"provider": "...", "model": "..."}, "reading_order": {...}}
    # Off by default: in testing, local models (gemma4:26b, qwen3-vl:8b) misjudged correct reading orders.
    verify_ai_reading_order: bool = False
    verify_sample_rate: float = 0.10  # share of unflagged figures the AI judge also reviews
    verify_ai_max_items: int = 150
    verify_max_pages: int = 400
    verify_max_output_tokens: int = 1500  # the judge explains problems and suggests alt text: more room than a writer

    localai_base_url: str = "http://localhost:8080/v1"
    localai_api_key: str = ""
    localai_default_model: str = "qwen3-vl-8b-instruct"
    # Thinking models (gemma 4, qwen3) spend the output budget reasoning and return empty answers;
    # "none" turns that off on LocalAI. Empty string omits the field.
    localai_reasoning_effort: str = "none"

    # AI gateway (LiteLLM). Every model the service offers is a LiteLLM model name; the portal's AI tiers
    # pick one per job. The key is a LiteLLM virtual key (or the master key in local testing).
    gateway_base_url: str = "http://litellm:4000/v1"
    gateway_api_key: str = ""
    gateway_default_model: str = "local-vision"
    gateway_table_model: str = ""  # empty -> the job's model
    gateway_json_mode: bool = False  # ask for JSON output; local backends vary, so parse leniently by default
    gateway_reasoning_effort: str = "none"  # passed through to backends that think (LocalAI); empty omits it

    # Adobe PDF Services (Autotag). Off unless switched on; documents sent here leave this service.
    # Every limit is configurable because Adobe doesn't publish them; set them from the contract.
    adobe_enabled: bool = False
    adobe_client_id: str = ""
    adobe_client_secret: str = ""
    adobe_base_url: str = "https://pdf-services-ue1.adobe.io"  # or https://pdf-services-ew1.adobe.io (EU)
    adobe_rpm: int = 25  # API requests per minute across all workers (token, upload, start, status, delete)
    adobe_max_concurrent: int = 2  # documents (chunks) at Adobe at once across all workers
    adobe_max_retries: int = 4  # retries for throttling (429 "too many requests") and server errors
    adobe_max_pages_per_call: int = 100  # larger documents are split, tagged in parts and merged
    adobe_max_mb_per_call: int = 100
    adobe_poll_seconds: float = 3.0
    adobe_timeout_minutes: int = 20  # per chunk, from upload to download
    adobe_generate_report: bool = True  # Adobe's own accessibility report (xlsx), kept with the job
    adobe_shift_headings: bool = False

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def flavours(self) -> list[str]:
        return [f.strip() for f in self.verapdf_flavours.split(",") if f.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
