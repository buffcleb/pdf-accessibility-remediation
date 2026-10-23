# AI models

[Operations](README.md) › AI models

## Contents

- [Roles](#roles)
- [The gateway](#the-gateway)
- [The local model server](#the-local-model-server)
- [Cloud models through OpenRouter](#cloud-models-through-openrouter)
- [AWS Bedrock](#aws-bedrock)
- [Without the gateway (development only)](#without-the-gateway-development-only)
- [Specialist notation](#specialist-notation)
- [OCR correction](#ocr-correction)
- [The kind-of-document classifier](#the-kind-of-document-classifier)
- [Verification settings](#verification-settings)

## Roles

All AI goes through the **AI gateway** (LiteLLM). The engine asks the gateway for a model name; the gateway decides
where that model runs.

| Role | Where it's set | Tested with |
|---|---|---|
| **Writer** (alt text, tables, reading order, notation, OCR corrections) | The job's AI level (and kind of document) → its gateway model; `GATEWAY_DEFAULT_MODEL` when a job has no level | `local-vision` = Qwen3-VL 8B on LocalAI |
| **Reviewer** (the AI review) | The job's AI level → its review model; `VERIFY_JUDGE_PROVIDER` / `VERIFY_JUDGE_MODEL` when the level has none | `local-judge` = Gemma 4 26B on LocalAI |

Keep the writer and the reviewer in **different model families**: a model tends to approve its own mistakes.

## The gateway

Models are defined in `config/litellm.yaml`, with hosts and keys from the engine's settings:

```
LITELLM_MASTER_KEY=sk-…                 # the gateway's own admin key
LITELLM_DB_PASSWORD=…
UI_USERNAME=admin                        # admin UI sign-in
UI_PASSWORD=…
LOCAL_AI_BASE_URL=https://your-localai-host/v1
LOCAL_AI_API_KEY=…
LOCAL_VISION_MODEL=openai/qwen3-vl-8b-instruct
LOCAL_JUDGE_MODEL=openai/gemma-4-26b-a4b-it-qat-q4_0
GATEWAY_BASE_URL=http://litellm:4000/v1  # how the engine reaches it
GATEWAY_API_KEY=sk-…                     # a virtual key (below); the master key only for local testing
GATEWAY_DEFAULT_MODEL=local-vision
VERIFY_JUDGE_PROVIDER=gateway
VERIFY_JUDGE_MODEL=local-judge
```

- **Adding models:** add entries to `config/litellm.yaml`, for the same family in the cloud, larger models or
  commercial models. Each writer needs a reviewer from a different family; the file has examples (`open-judge` for
  Qwen writers and the small Claude one, `commercial-judge` for the larger Claude ones). Set their keys, then restart
  the gateway. A super admin then publishes each writer as an AI level and picks its review model.
- **Writers the file ships with:** `open-small-vision` (the local model's family, hosted), `open-large-vision` (a
  much larger model of that family), `commercial-small-vision` (a commercial vendor's small model, for a cheaper
  paid level), `commercial-medium-vision` and `commercial-large-vision` (its largest, for a kind of document such
  as *Math and science*). Reviewers: `open-judge` and `commercial-judge`. A name says family, size and role, never
  the model or its version, so the model behind it can be replaced without touching the AI levels.
  Renaming or removing an entry leaves any level or kind that names it without a model: pick another for it in
  **Administration > Services and AI levels**.
- **Reviews follow the level:** a document's AI review, and later re-reviews of its saved versions, use the review
  model of the level (or kind) it was processed with, so a cloud level's documents aren't reviewed by a local model,
  or the other way round. The review's cost is part of the job's AI cost.
- **Admin UI** at the gateway's `/ui` (locally `http://localhost:4000/ui`). Super admins get a link to it in the
  portal when `PORTAL_GATEWAY_ADMIN_URL` is set (the local stack sets it; on Kubernetes it's `GATEWAY_ADMIN_URL` in
  `deploy.env`, empty by default because the UI isn't exposed there: use `kubectl port-forward svc/litellm 4000`).
  Without an address the same place in the portal opens a short note on how to reach it:
  - models, with a playground;
  - spend per model, key and tag (every call is tagged `job:<id>` and `tier:<key>`);
  - request logs and errors;
  - **virtual keys:** give the engine its own key with a budget and rate limit, instead of the master key.
- **Signing in to the admin UI:** `UI_USERNAME` and `UI_PASSWORD` (in the engine's settings) are a shared login
  with full control, and the UI warns about it. Give each admin an account of their own (Internal Users, with
  the admin role), check they can sign in, then turn the shared login off: `GATEWAY_SHARED_LOGIN=off` in
  `deploy/k8s/deploy.env` (it adds `disable_env_credential_login: true` to the gateway's `general_settings`; with
  Docker Compose, add that line to `config/litellm.yaml` yourself). The engine isn't affected: it uses the master
  key, not this login.
- **Spend backstop:** `max_budget` and `budget_duration` in `config/litellm.yaml` (as shipped, $150 per 30 days;
  set it for your deployment) cap total spend, on top of the portal's page quotas. Local models are listed at zero cost.
- **Cost per job:** the gateway reports each call's cost; the engine adds it up (`metrics.ai_cost_usd`,
  `verification_ai_cost_usd`), and super admins see it on the job page and in the Cost report.

## The local model server

Any OpenAI-compatible server works behind the gateway (LocalAI, vLLM, Ollama). Use models with image support (a
projector, `mmproj`, in the model config). Tested GPU layout: Gemma 4 26B (about 16 GB) on an RTX 3090, Qwen3-VL 8B
(about 7 GB) on an RTX 4060 Ti 16 GB. To pin a model to a card in LocalAI, set in its YAML:

- `gpu_layers: 99`
- `main_gpu: "N"`
- `tensor_split` (for example `"1,0"`)
- `context_size: 8192`

Then restart LocalAI and check with `nvidia-smi`. Thinking models (Gemma 4, Qwen3) need `reasoning_effort: none`,
which the engine sends (`GATEWAY_REASONING_EFFORT=none`); without it they can return empty answers.

## Cloud models through OpenRouter

Add OpenRouter models to `config/litellm.yaml` (with `OPENROUTER_API_KEY`) and publish them as AI levels. Prices are
OpenRouter list prices per million tokens (input / output), checked October 2026:

| Profile | Writer | Reviewer | Notes |
|---|---|---|---|
| Same as local, hosted | `qwen/qwen3-vl-32b-instruct` ($0.10 / $0.42) | `google/gemma-4-31b-it` ($0.09 / $0.34) | The local model families, larger; the cheapest path to the cloud |
| Quality | `anthropic/claude-sonnet-5.5` ($2 / $10) | `google/gemini-3.8-flash` ($0.75 / $3.75) | Best for math-heavy documents ([test report](../evaluation/test-reports/2026-10-07-notation-ai-tiers.md)) |
| Budget | `google/gemini-3.1-flash-lite` ($0.25 / $1.50) | `openai/gpt-5.4-nano` ($0.20 / $1.25) | Lowest cost from commercial vendors |
| Handwriting-heavy | `openai/gpt-5.4-mini` ($0.75 / $4.50) | `anthropic/claude-sonnet-5.5` | The GPT-5 family reports the best handwriting accuracy |

With any cloud model, page images leave your infrastructure: check the provider's data-retention terms before sending
documents with personal, student, patient or personnel information, mark the level **external**, and avoid `:free`
model variants (free endpoints may log prompts).

## AWS Bedrock

The gateway also serves models from AWS Bedrock, under an AWS agreement rather than OpenRouter's: AWS doesn't train on
prompts or answers, or pass them to the model's maker, and keeps no copy unless model invocation logging is turned on
(it's off by default; leave it off). `config/litellm.yaml` has one so far: **`trial-a-vision`**, Amazon's Nova Lite
($0.06 / $0.24 per million tokens, reads images), a writer for comparing with `commercial-small-vision` (Claude Haiku).
Pair it with `open-judge`. Amazon's own models are usually available to an account straight away; other makers' newest
ones may not be (below).

**In the AWS account** (the console's region: US East, N. Virginia):

1. **A budget** with an alert (Billing › Budgets), on the whole account: Claude and other makers' models are billed
   as AWS Marketplace items, not under "Amazon Bedrock".
2. **The models available to the account.** Each model's **cross-region** tokens-per-minute quota (Service Quotas ›
   Amazon Bedrock) must be above 0; request the default if it isn't. AWS may refuse a new model for an account with
   little history ("not available for this account"); it may open up later, or the account's AWS team can ask.
3. **A first call from someone allowed to subscribe** (a console user, in Bedrock's chat playground or CloudShell):
   it accepts the maker's terms for the whole account. The gateway's own user can't, by design.
4. **A user for the gateway** with no console access and only `bedrock:InvokeModel` and
   `bedrock:InvokeModelWithResponseStream`, on each model's `us.` inference profile and the models behind it in
   every region (`arn:aws:bedrock:*::foundation-model/…`): a cross-region call needs both. An access key for it, kept
   in a password manager. (On premises for production: IAM Roles Anywhere, for short-lived credentials instead.)

**In the service:** the key in `engine.env` (or `.env` locally) as `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`,
with `AWS_REGION_NAME=us-east-1`; `deploy.sh` puts them in the gateway's secret only. Then an AI level for
`trial-a-vision` with `open-judge` (Administration › Services).

**Choosing models:** use `us.` inference profiles (they stay in US regions), never `global.` ones, and set each
model's Bedrock price in its `model_info` so the portal's costs are right. A reviewer is still from a different family
from its writer, and on Bedrock a model the review needs may be missing: Google's Gemma runs only on Bedrock's
separate "mantle" endpoint, so `open-judge` stays on OpenRouter until a Bedrock reviewer is chosen (Nova Lite is
the cheapest one that reads images).

## Without the gateway (development only)

The engine can call LocalAI, Ollama or OpenRouter directly (`ai_provider=localai|ollama|openrouter`, with the
`LOCALAI_*`, `OLLAMA_*`, `OPENROUTER_*` settings). Portal jobs without an AI level use `PORTAL_ENGINE_AI_PROVIDER`.
Prefer the gateway everywhere else: direct calls have no spend tracking, budget or per-job attribution. On the
cluster there's no choice: the engine has no internet access, and with `AI_GATEWAY_ONLY=true` (set by the
manifests) a direct provider becomes the gateway's `GATEWAY_DEFAULT_MODEL`, and a direct judge its
`GATEWAY_JUDGE_MODEL` (default `local-judge`) (#361).

## Specialist notation

Notation the layout rules can't read (math symbols with no Unicode meaning, sums and integrals, chemistry, dosages,
engineering units) is read by the job's AI model ([how](../architecture/engine-pipeline.md#math-and-specialist-notation)).

- `AI_NOTATION_ENABLED` (default true) switches it off for the engine, and `AI_NOTATION_MAX` (default 100) caps the
  regions per document, taking the first in page order (the report says how many were left out; raise it for
  math-heavy collections).
- Each AI level has **Read specialist notation with AI**, on by default.
- Every reading is flagged and reviewed; readings that don't match the page aren't used
  (`metrics.notation_read_by_ai`, `metrics.notation_ai_rejected`).
- **Quality follows the model.** On a research paper, the local 8B model was rarely wrong but often imprecise; a
  commercial model read about two thirds of formulas exactly. Use a stronger level, or a *Math and science* kind, for
  math-heavy documents where accuracy matters ([test report](../evaluation/test-reports/2026-10-07-notation-ai-tiers.md)).
- **A second reading of formulas, from their pictures** (#403): the optional formula reading service
  (`docker/formula`; `FORMULA_OCR_ENABLED`, default false, on when the deployment runs it) reads each formula's image,
  and its reading is checked against the AI's. `FORMULA_OCR_MAX` (200) caps the formulas per document;
  `FORMULA_OCR_TIMEOUT_S` (180) is how long to wait for an answer.
- **The Adobe route** reads notation too: the readings go onto Adobe's matching text tags in the improved copy
  ([how](../architecture/adobe-route.md)).

When a document has two output files, a figure or formula that appears in both is reviewed once.
`VERIFY_MAX_OUTPUT_TOKENS` (default 1500) gives the reviewer room for its explanation.

## OCR correction

On scanned pages the text comes from the Tesseract command line (in the engine image), which reports a confidence for
each word. Words below `OCR_AI_MIN_CONF` (80) are corrected by the job's AI model from the line image; lines with a
mean confidence below `OCR_AI_TRANSCRIBE_BELOW` (45) are transcribed afresh. Corrections must pass checks: confident
words stay, changes look like the word they replace, no added words, no other writing systems, transcriptions fit the
line.

- `OCR_AI_MAX_LINES` (300) caps the lines per document, most uncertain first. Each AI level has **Correct OCR with
  AI**, on by default.
- `OCR_ENGINE=pymupdf` switches back to PyMuPDF's built-in OCR (no confidences, so no AI correction). In testing it
  read barely legible scans better and everything else worse.
- Results: `metrics.ocr_ai_words_corrected`, `ocr_ai_lines_transcribed`, `ocr_ai_rejected`, and `reports.ai_ocr`.
- **Not tested yet:** scanned handwriting. Treat AI transcriptions of handwritten pages as drafts.
- Measured accuracy: [test report](../evaluation/test-reports/2026-10-07-ocr-ai-correction.md).

Figures printed on scanned pages are found and tagged automatically; see
[Scanned pages](../architecture/engine-pipeline.md#scanned-pages).

## The kind-of-document classifier

**Let AI decide** (Settings → Uploads and limits) asks the engine's `POST /api/v1/classify` which of an AI level's
kinds a document is, before the portal sends it. The engine reads the first pages' text (no images) and asks either:

- **Jev** (TypeSafe's `jev-1.13` through OpenRouter's System One API, using `OPENROUTER_API_KEY`; the model is
  `CLASSIFIER_JEV_MODEL`). On the cluster the engine calls the gateway's `/systemone` route
  (`config/litellm.yaml`), which adds the key, so only the gateway holds it. Text only, about $0.042 per million
  input tokens (about $0.0001 per document), and not trained on requests. Local-only groups never use it.
- or **a gateway model** you choose (local, for local-only groups).

If the classifier can't be reached, the document goes on with General, so it never holds up the queue. On 30 sample
documents (math theses, formula sheets, forms, sheet music, policies, catalogs), Jev picked the expected kind for all
of them (median 0.6 s), and the local 8B model for 29.

## Verification settings

| Setting | Default | Meaning |
|---|---|---|
| `VERIFY_ENABLED` | `true` | Run tag verification on every output |
| `VERIFY_AI_ENABLED` | `true` | Run the AI review layer |
| `VERIFY_SAMPLE_RATE` | `0.10` | Share of unflagged figures also reviewed |
| `VERIFY_AI_MAX_ITEMS` | `150` | Cap on AI reviews per output |
| `VERIFY_AI_READING_ORDER` | `false` | The AI reading-order check (unreliable with the tested local models) |
| `VERIFY_ROUTES` | empty | JSON: a different reviewer per content type, e.g. charts |
| `VERIFY_MAX_OUTPUT_TOKENS` | `1500` | Room for the reviewer's answer |
| `VERIFY_MAX_PAGES` | `400` | Pages per output the verification looks at |
| `VERIFY_CONTRAST` | `true` | List text below WCAG's contrast minimum, per page, for review |
