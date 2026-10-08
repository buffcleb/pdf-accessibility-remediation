# Tests

Two suites, one per service. GitHub Actions runs both, plus the web app's type-check and build, on every pull
request (`.github/workflows/ci.yml`).

| Folder | What it covers | Needs |
|---|---|---|
| [`engine/`](engine) | The remediation engine (`app/`): layout analysis, tagging, existing tags, OCR, specialist notation, verification, the AI gateway and adapters (with HTTP mocked), veraPDF parsing, the API, the Adobe route (against the fake Adobe), the PDF report | Python only. A few tests also need Tesseract, Java and veraPDF, or Pango (for WeasyPrint); they're skipped when those aren't installed |
| [`portal/`](portal) | The portal (`portal/`): sign-in, roles, groups, services and pages, uploads and jobs, versions, help requests, reports, email, backups | A PostgreSQL database on port 55432 |

No test calls a real AI model or a real Adobe account.

## Running them

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

**Engine:**

```bash
.venv/bin/python -m pytest -q tests/engine
```

**Portal:** start a throwaway PostgreSQL first. Each test run empties it.

```bash
docker run -d --rm --name portal-test-db -e POSTGRES_USER=portal -e POSTGRES_PASSWORD=test -e POSTGRES_DB=portal -p 55432:5432 postgres:16-alpine
```

```bash
.venv/bin/python -m pytest -q tests/portal
```

Use another database with `PORTAL_TEST_DATABASE_URL`. **Never point it at a real one:** the tests truncate its
tables.

**Everything:** `.venv/bin/python -m pytest -q` (the database is still needed).

### The tests that need system tools

The engine image has everything, so the full suite can run there:

```bash
docker compose run --rm -u 0 -v "$PWD:/repo" -w /repo -e PYTHONPATH=/repo worker \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q tests/engine"
```

## Layout

- `engine/conftest.py` sets the engine's test environment (job data under `.pytest_data/`, Redis and Celery in
  memory, the mock AI allowed) and builds the sample PDFs (`harness/make_samples.py`) once per run.
- `portal/conftest.py` sets the portal's test environment (the test database, in-memory storage and mail, test
  signing keys), applies the migrations, and provides fixtures for groups, users and signed-in clients.
- Tests are named for the behavior they check (`test_a_printed_code_signs_in_once`); helpers shared within a
  suite are imported from the test module that defines them (for example `tests.portal.test_jobs`).
