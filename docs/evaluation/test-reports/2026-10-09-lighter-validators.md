# Lighter PDF/UA checkers compared with veraPDF

**Date:** 9 October 2026 · **Code:** main at `1735643` (issue #64) · **AI:** none

## Question

veraPDF needs Java: a JRE in the engine image, and a new JVM for every check. Could a lighter checker replace it,
for some checks or all of them, without losing what the engine relies on?

## Setup

- **Documents:** the 30 files in `samples/FullRun` (forms, slide decks, theses, papers with math, scans, a
  catalog), plus the engine's remediated output for each: 60 files. veraPDF fails all 30 originals and passes all
  30 outputs, so both kinds of disagreement show up.
- **Reference:** veraPDF as the engine runs it (PDF/UA-1 profile), in the engine image.
- **Candidates:**

  | Tool | Version | Language | Licence | First release | What it is |
  |---|---|---|---|---|---|
  | [pdfua](https://pypi.org/project/pdfua/) | 0.1.3 | Python (pikepdf) | MIT | 4 Oct 2026 | 12 checks covering 16 of the 106 machine-checkable PDF/UA-1 rules (its own count: 15%) |
  | [outloud](https://pypi.org/project/outloud/) | 0.2.1 | Python (pikepdf, PyMuPDF) | Apache-2.0 | 17 Sep 2026 | PDF/UA-1 conformance rules mapped to Matterhorn, plus "semantic" checks (alt text, tables, links) |
  | [avalpdf](https://pypi.org/project/avalpdf/) | 0.1.10 | Python | none declared | Feb 2025 | Not run: it is built on the PDFix SDK, which was set aside earlier (the unlicensed SDK changes its output), and declares no licence |
  | [Arlington PDF Model](https://github.com/pdf-association/arlington-pdf-model) checker | veraPDF Arlington 1.30.2 | Java (a REST service, Docker image `verapdf/arlington`) | open source (model: Apache-2.0) | 2021 (model) | Checks every object in the file against the PDF specification's object model (required keys, types, allowed values). Not PDF/UA. The PDF Association's own C++ checker, TestGrammar, is a proof of concept that has to be built against a PDF library, so veraPDF's service was used |

- The Arlington checker ran as veraPDF's service on this machine (an amd64 image under emulation), with the
  PDF 1.7 model (2.0 for files that declare 2.0). It's a different kind of check, so it's compared with what
  the engine does to a file rather than with veraPDF's verdict.
- Both Python tools were installed in a separate environment from wheels (no install-time code), after reading
  their code: neither calls the network (outloud's optional `--view` serves a page on 127.0.0.1).

## Method

- **Verdict:** pass or fail per file. veraPDF passes a file with no failed rules; pdfua and outloud pass it with no
  error-level conformance findings.
- **Clauses:** each tool's findings mapped to ISO 14289-1 clause families (7.1, 7.2, 7.21 …) and compared with
  veraPDF's failed rules per file: caught, missed, and extra. This is approximate: the tools sometimes file the
  same requirement under a different clause (outloud puts MarkInfo under 7.1; veraPDF reports it as 6.2).
- **Time and memory:** wall time and peak memory per file. veraPDF in the engine image (Linux container); the
  Python tools on the host (macOS, Apple silicon).

## Results

| | veraPDF | pdfua | outloud |
|---|---|---|---|
| Verdicts that agree with veraPDF | — | 49 of 60 | 51 of 60 |
| Originals passed (all fail veraPDF) | 0 | 2 | 0 |
| Outputs failed (all pass veraPDF) | 0 | 9 | 9 |
| veraPDF clause families caught | — | 99 of 132 | 94 of 132 |
| Time per file, average (range) | 1.3 s (0.8–3.5) | 0.4 s | 1.4 s |
| Peak memory | 570 MB | 137 MB | 244 MB |

- **pdfua misses font problems entirely.** veraPDF found clause 7.21 failures (embedding, glyph widths, CIDSet) in
  13 originals; pdfua flagged none of them, and passed 2 failing files. Font failures are what decide the
  engine's modified file and which pages to flatten.
- **outloud's misses are mostly bookkeeping.** Of its 38 misses, 28 are clauses 6.2 and 5 (MarkInfo, the PDF/UA
  identifier), which it checks but files under 7.1. It caught font problems in 12 of the 13 files.
- **Their extra failures on the outputs are mostly tables.** 47 tables have no header cells, and 14 have rows of
  different lengths. veraPDF's profile doesn't machine-check either (the Matterhorn Protocol leaves headers to a
  person), so these are real accessibility questions, not PDF/UA failures. The rest:
  - pdfua: one image it reads as drawn outside any tag (veraPDF accepts it; likely inside a form XObject).
  - outloud: one font without a CIDToGIDMap; and, as semantic checks, empty links (9 files) and 16 lines of prose
    marked as artifacts in one file.
- **veraPDF's time is mostly starting Java.** The 30 outputs take 42 s checked one by one, and 11.5 s in one
  JVM. A small file takes 1.36 s.
- **veraPDF runs in less memory with a smaller heap.** With `JAVA_OPTS="-Xmx256m -XX:+UseSerialGC
  -XX:TieredStopAtLevel=1"`, the largest file checked fine in 2.6 s at 212 MB peak.

### Arlington

- **It found one problem the engine introduces.** In 5 outputs, the font the engine embeds for invisible text
  (OCR and flattened pages) had a descriptor named "DejaVu Sans Book" where the PDF specification requires the
  font's own name, "DejaVuSans". veraPDF doesn't test this. Fixed in the same change as this report
  (`app/pipeline/fonts.py`, with a test).
- **Otherwise the engine only removes problems.** Outputs lost failures their originals had: tags with no
  parent, pages with tagged content but no `StructParents`, a structure tree whose parent tree or ID tree was
  the wrong type.
- **What it reports in the originals** is mostly producers' leftovers: extra keys in structure elements and
  the catalog, PDF 2.0 keys in 1.7 files, link actions naming destinations that don't exist. 27 different rules
  failed across the 60 files; most of them don't affect accessibility.
- **Speed and memory:** 1.1 s per file on average (0.05–8.6 s) with the service already running, and about
  1 GB of memory for the service (Java's default heap). It's Java, so it doesn't reduce the footprint.

## What the engine needs from the checker

The engine uses more than a verdict. From veraPDF's report it takes:

- each failed rule by clause and test number, for the reports' rule-by-rule table;
- the pages and fonts in each failed check, to choose the pages to flatten;
- the glyph widths in 7.21.5 failures, to fix them without changing the page;
- the 7.21.4.2 glyph-list failures it repairs.

Neither tool reports at that level, so neither can stand in for veraPDF in the after-check.

## Conclusion

- **Keep veraPDF as the validator.** pdfua covers too little (no fonts). outloud agrees well, but it's three
  weeks old and doesn't give the detail the fixes need. avalpdf doesn't fit (above).
- **Arlington is a complement, not a replacement:** it checks the file against the PDF specification, not
  PDF/UA, and it's Java as well. It's worth running over the sample documents when the engine changes how it
  writes files (it found the font name problem), not on every job.
- **Make veraPDF lighter instead:**
  - cap the JVM heap, which cut peak memory from 570 MB to about 210 MB here;
  - start Java less often. Every check currently runs twice, once for PDF/UA-1 (the verdict) and once for
    PDF/UA-2 (shown for information), so a job starts Java at least five times (before and after, two each,
    plus the PDF report's own check), and more when fonts are repaired.
- **outloud's semantic checks are worth watching**: headerless tables, empty links and prose marked as artifacts
  are the kind of thing the review list exists for. It's too new to depend on today.

## What changed because of it

- veraPDF now also runs as a service kept running (its REST server), so a check doesn't start Java. With the
  service set to report like the command line (up to 200 failed checks per rule, with messages), all 60 files gave
  identical results: the verdict, failed rules and counts, pages, fonts and glyph widths. Only the order of the
  rules differed, so the engine now sorts them by clause either way. A check took a median of 0.35 s, even with
  the amd64 image emulated on Apple silicon. Peak memory was about 850 MB with a 512 MB heap.
- It restarts hourly on Kubernetes, and the engine falls back to the command line when the service doesn't answer.
  Super admins choose between the two, and whether to also check PDF/UA-2
  ([configuration](../../operations/configuration.md#verapdf-service)).
- The embedded text font's name, found by the Arlington checker, is fixed.

## Limits

- 60 files. The tools were timed on different machines (veraPDF in the engine's Linux container, the Python
  tools on macOS), so compare the times roughly.
- The clause mapping is approximate (above).
- Both Python tools are early releases and change quickly; results may differ with later versions.

## Reproducing it

Install `pdfua==0.1.3` and `outloud==0.2.1` in a separate environment. Run `pdfua check -f json` and
`outloud --json` on each file, and veraPDF with `-f ua1` in the engine image. Then map each finding to its clause
family (outloud's rule catalogue lists the clause for every rule). For Arlington, run `docker run -p 127.0.0.1:8080:8080
verapdf/arlington:v1.30.2` and POST each file to `/api/validate/arlington1.7` (or `arlington2.0`) with
`Accept: application/json`. Then compare each output's failed rules with its original's.
