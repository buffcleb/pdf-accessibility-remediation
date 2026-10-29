# Tests

Two Python suites, one per service, plus the web app's own tests (`web/tests`). GitHub Actions
(`.github/workflows/ci.yml`) runs them on pull requests and pushes to `main`, each only when files it tests have
changed: the engine and portal suites, the web app's type-check, build and tests, and checks of the shell scripts
(shellcheck), the SQL (bandit) and the dependencies (pip-audit).

| Folder | What it covers | Needs |
|---|---|---|
| [`engine/`](engine) | The remediation engine (`app/`): layout analysis, tagging, existing tags, OCR, specialist notation, verification, the AI gateway and adapters (with HTTP mocked), veraPDF parsing, the API, the Adobe route (against the fake Adobe), the PDF report | Python only. A few tests also need Tesseract, Java and veraPDF, or Pango (for WeasyPrint); they're skipped when those aren't installed |
| [`portal/`](portal) | The portal (`portal/`): sign-in, roles, groups, services and pages, uploads and jobs, versions, help requests, reports, email, backups | A PostgreSQL database on port 55432; without one the tests are skipped |
| [`a11y/`](a11y) | Not tests: `serve.py` runs the portal API with invented people and documents for `scripts/a11y.sh` | See below |

No test calls a real AI model or a real Adobe account.

## Running them

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

**Engine:**

```bash
.venv/bin/python -m pytest -q -n auto tests/engine
```

`-n auto` (pytest-xdist) runs the tests side by side, one worker per CPU: about a minute instead of four. Leave it
out to run them in one process, for example to step through one with a debugger.

**Portal:** start a throwaway PostgreSQL first. Each test run empties it. These tests share the one database, so
they run in one process (no `-n`).

```bash
docker run -d --rm --name portal-test-db -e POSTGRES_USER=portal -e POSTGRES_PASSWORD=test -e POSTGRES_DB=portal -p 55432:5432 postgres:16-alpine
```

```bash
.venv/bin/python -m pytest -q tests/portal
```

Use another database with `PORTAL_TEST_DATABASE_URL`. **Never point it at a real one:** the tests drop and recreate
its schema. With no database the portal tests are skipped; set `PORTAL_TEST_DATABASE_REQUIRED=1` (as CI does) to
make that a failure instead.

**Everything:** `.venv/bin/python -m pytest -q` (the database is still needed).

**Web app:** in `web/`, `npm ci`, then `npm run build` (type-check and build) and `npm test` (the tests in
`web/tests`, with Node's own test runner).

**Accessibility:** `scripts/a11y.sh` checks the web app's main pages with axe-core in a headless browser (WCAG 2.2 A
and AA, light and dark, as each role). It needs the `.venv`, `web/`'s packages and a Playwright browser
(`npx --prefix web playwright install chromium`); it uses `PORTAL_TEST_DATABASE_URL` if set, or else starts a
throwaway PostgreSQL container. `A11Y_ONLY=admin` checks only the pages whose name contains "admin". CI runs it only
when started by hand (`.github/workflows/a11y.yml`).

**Regression documents:** `scripts/regression.sh` runs real documents that once broke something through the code
as it stands, in the engine's image; see [harness/README.md](../harness/README.md#regression-documents).

### The tests that need system tools

The engine image has everything, so the full suite can run there:

```bash
docker compose run --rm -u 0 -v "$PWD:/repo" -w /repo -e PYTHONPATH=/repo worker \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q -n auto tests/engine"
```

## Layout

- Each suite has a README listing every test file: what it checks, what it needs, the to-dos and PRs it belongs to,
  and how to run one file or one test: [engine/README.md](engine/README.md), [portal/README.md](portal/README.md).
- `engine/conftest.py` sets the engine's test environment (job data under `.pytest_data/`, Redis and Celery in
  memory, the mock AI allowed) and builds the sample PDFs (`harness/make_samples.py`) once per run.
- `portal/conftest.py` sets the portal's test environment (the test database, in-memory storage and mail, test
  signing keys), applies the migrations, and provides fixtures for groups, users and signed-in clients.
- Tests are named for the behavior they check (`test_a_printed_code_signs_in_once`); helpers shared within a
  suite are imported from the test module that defines them (for example `tests.portal.test_jobs`).
