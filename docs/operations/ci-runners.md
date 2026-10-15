# CI on your own runners

The tests in `.github/workflows/ci.yml` run on GitHub's runners by default. On a private repository those are
billed by the minute against a monthly allowance. The same jobs can run on a machine of your own at no
charge.

The workflow chooses where to run from one repository variable:

| `CI_RUNNER` (Settings → Secrets and variables → Actions → Variables) | Jobs run on |
|---|---|
| not set | GitHub's runners (`ubuntu-24.04`) |
| a runner's label, for example `pdf-ci` | that self-hosted runner |

Delete the variable to go back to GitHub's runners, for example while the runner's machine is down.

## Where to put a runner

**Not on the machines that run the service.** On 11 October 2026 a runner set was installed on the service's own
cluster with GitHub's Actions Runner Controller, and three test jobs started at once. Twenty-three seconds
after the last of them started, the network card of the physical host under all three nodes stalled (an Intel
e1000e "Detected Hardware Unit Hang", repeating until the host was powered off and on 17 minutes later). The
host was alive but unreachable, and so was the service. Its data came back intact.

The host's log rules out memory and a crash. The jobs' simultaneous downloads are the likely trigger, on a
card with a known weakness under heavy sending, though the log can't prove it. Whatever the trigger, CI
shares a network card, a disk and a power supply with anything on the same host, and a test run isn't worth
the service.

Use a separate Linux machine or virtual machine on other hardware:

- Linux, with Docker: the portal tests start PostgreSQL as a service container, which GitHub supports only on
  Linux runners.
- Outbound HTTPS to github.com, GitHub's package and action downloads, PyPI, npm, the Ubuntu archive,
  software.verapdf.org and public.ecr.aws. Nothing inbound.
- 2 CPUs and 4 GB of memory are enough for one job at a time.
- Register it under the repository's Settings → Actions → Runners → New self-hosted runner, give it a label
  (for example `pdf-ci`), and set the repository variable `CI_RUNNER` to that label.
- The runner must be version 2.327.1 or later: the workflow's actions (checkout v7, setup-python v7, setup-node v7,
  paths-filter v4) run on Node.js 24, which older runners don't have. A runner left to update itself stays current.
- One runner takes one job at a time, so a pull request's jobs run one after another. That is the safe
  default; add runners only on hardware with room for them.

**Only on a private repository.** A self-hosted runner executes whatever a pull request's workflow says. On a
public repository anyone can open a pull request, so don't attach a runner to the public snapshot repository.

## Running it by hand

The workflow can also be started from the Actions tab (CI → Run workflow) on any branch. Started that way,
all four jobs run whatever changed.

## Which jobs run

Each job runs only when files it tests have changed:

| Job | Runs when these change |
|---|---|
| Engine tests | `app/`, `tests/engine/`, the requirements files, `pytest.ini` |
| Portal tests | `portal/`, `tests/portal/`, the requirements files, `pytest.ini` |
| Web build | `web/` |
| Shell scripts | `scripts/`, `docker/web/` |
| Dependency audit | the requirements files, `web/package-lock.json` (known advisories against what they resolve to; a hit fails the job, and the fix is a version bump or, for one that doesn't apply, a named exception in the workflow) |

A change to the workflow itself runs all five. A change to documentation alone runs none: only the short
"What changed" job appears.
