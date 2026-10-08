# Specialist notation read by AI: Local, Cloud and Commercial on a research paper

**Date:** 7 October 2026 · **Code:** PR #19 (E1, issue #17), commits `9576b7e`–`cca8373` · **Document:** a
17-page mathematics research paper from arXiv (`2610.01711v1.pdf`)

## Question

E1 adds an AI fallback for notation the layout rules can't read: symbols with no readable meaning, sums,
integrals and roots, norms, set and logic symbols, chemistry, dosages and engineering units. Does it work on a
real math-heavy document, and how much does the AI option matter?

## Setup

- **Document:** an arXiv paper with dense research math: norms (∥F∥), "less than or approximately equal to"
  (≲), Legendre symbols, sums with limits, primes, Greek letters. Many displayed equations are extracted
  from the PDF as several fragments, some with characters that have no Unicode meaning.
- **Processing:** the standard route through the portal, as a normal user, with **review every figure**
  switched on. 1 of 17 pages needed flattening (an unembeddable font), so each run produced two files: the
  main (modified) file and a visually exact file.
- **AI options:**

  | Option | Writer | Reviewer |
  |---|---|---|
  | Local AI | Qwen3-VL 8B (LocalAI, on our own hardware) | Gemma 4 26B (LocalAI) |
  | Cloud AI | Qwen3-VL 8B (the same model as Local, hosted on OpenRouter) | Gemma 4 31B (OpenRouter) |
  | Commercial AI | Claude Sonnet 5.5 (OpenRouter) | Gemini 3.8 Flash (OpenRouter) |

## Method

1. Each option processed the paper through the portal, one after another, on the same code.
2. Each option's own reviewer checked every figure and formula, as it would in normal use.
3. **For a fair comparison of the writers, the same reviewer scored both paid options.** Cloud's notation
   readings were scored again by Commercial's reviewer, Gemini Flash, so Cloud and Commercial are judged
   alike. Local's numbers come from its own reviewer and aren't directly comparable.
4. Readings were sampled and read by hand to see whether the reviewer's verdicts were fair.

The reviewer is an AI, not a person, so "accurate" means "accurate according to Gemini Flash". Sampling
suggested its verdicts were sensible, while the Local reviewer's were noisier (see below).

## Results

| | Local AI | Cloud AI | Commercial AI |
|---|---|---|---|
| Time (17 pages) | 3 min 30 s | 6 min 26 s | 5 min 43 s |
| Cost: writing + review | $0 | $0.015 + $0.016 = **$0.03** | $0.42 + $0.55 = **$0.96** |
| Regions sent to the AI | 82 | 82 | 82 |
| AI readings used | 70 | 62 | **71** |
| Rejected by the consistency check (didn't match the page) | 12 | 20 | 11 |
| Main file passes PDF/UA-1 (veraPDF) | yes | yes | yes |
| Visually exact file | 1 failed check (font, needs a visual change) | same | same |

Failed PDF/UA-1 checks went from 13,496 in the original to 0 in the main file on every option.

**Notation readings, scored by the same reviewer (Gemini Flash):**

| | Fully accurate | Partly right | Wrong |
|---|---|---|---|
| Cloud AI (58 of 62 readings scored) | 15 (26%) | 41 (71%) | 2 (3%) |
| Commercial AI (71 readings) | ~48 (68%) | 22 (31%) | 1 (1%) |

Cloud almost never reads a formula *wrongly*, but it's often *imprecise*: it misses a symbol's proper name or
part of a limit. Commercial gets about two thirds exactly right.

Cloud uses the same writer model as Local, so **Cloud's score is a fair estimate for Local too**: about a
quarter of readings fully accurate and nearly all of the rest partly right.

**Local AI**, scored by its own reviewer, had most readings marked inaccurate. Sampling showed about half of
those were real errors by the 8B writer (for example "≲" read as "less than or equal to", which changes the
meaning). The rest were false alarms or nitpicks by the Local reviewer, such as "'b sub H' instead of 'b sub
H'", or "'to the one-half' instead of 'to the power of one-half'".

### Examples (the same formula)

| On the page | Cloud AI | Commercial AI |
|---|---|---|
| a Legendre symbol (α/p) times a sum | "equals the fraction alpha over p times the sum from m equals 0 to p minus 1 of …" | "equals the **Legendre symbol** of alpha over p, times the sum from m equals 0 to p minus 1 of …" |
| ∥b∥_A1 ≤ (2 − ε)/√(1 + (2 − ε)²) | "the norm of b in A subscript 1 less than or equal to 2 minus epsilon over the square root of 1 plus 2 minus epsilon squared" | "the norm of b, subscript A 1, is less than or equal to the fraction with numerator 2 minus epsilon and denominator the square root of 1 plus, open parenthesis, …" |

Cloud's "alpha over p" reads a Legendre symbol as a fraction, which is a different thing. Commercial names it.
Commercial also says where fractions start and end, which matters when listening.

**Local AI (Qwen3-VL 8B) examples, from an earlier run on the same paper:**
- "|α|² + |β|² = 1" was read as "the absolute value of alpha squared plus the absolute value of beta squared
  equals 1". Correct.
- A 2×2 matrix was read as "the matrix with rows alpha beta and negative beta alpha". Correct.

## What testing changed

Running real documents found problems the unit tests hadn't. All were fixed in PR #19 before this comparison:

1. **AI readings were lost from the main file.** When a page needs flattening, the engine re-analyzes the
   document for the modified file, and that re-analysis didn't carry the readings over. The first run's main
   file had the layout rules' readings. They're now re-applied, with no second round of AI calls.
2. **Raised primes read as powers** ("F to the power of ′"). The layout rules now say "F prime", "f double
   prime".
3. **The consistency check rejected good readings** when the model wrote LaTeX command words into its plain
   transcription ("left(4a²/pright)"). Command words and LaTeX Greek letters are now handled.
4. **Too few formulas went to the AI.** Formulas with norms, set and logic symbols, or several
   sub/superscripts with Greek letters were left to the rules, which read them poorly. They're now sent to
   the AI. Simple math (powers, fractions) still isn't.
5. **"≲" read as "≤".** The writer's prompt now includes a short guide to how research-math symbols are
   read aloud.
6. **Reviews:**
   - The same formula in both output files was reviewed twice. Each formula is now reviewed once, and 67 of
     77 reviews were reused on the Local run.
   - One long review answer was cut off. Answers are now asked to be brief, with more room
     (`VERIFY_MAX_OUTPUT_TOKENS`).

Before any of this, a check across all sample documents found false triggers, which were fixed:
- "iv" (a roman numeral) taken as "i.v."
- "U.S.C." taken as "s.c."
- "bid" taken as "b.i.d."
- math arrows taken as chemistry
- lone symbol glyphs

Ordinary documents now send nothing to the AI.

Local AI run on the same paper, before and after the fixes:

| | Before | After |
|---|---|---|
| Time | 4 min 18 s | 3 min 30 s |
| AI readings used / rejected | 52 / 18 | 70 / 12 |
| Reviews reused in the second file | 0 | 67 of 77 |
| Review answers cut off | 1 | 0 |

## Conclusions

- **The fallback works.**
  - The AI catches the notation the rules can't read.
  - The consistency check rejects readings that don't match the page: mostly ones where the AI read part of
    a neighboring line, or invented characters.
  - Every reading is flagged for a person to check.
- **Quality follows the model.** For math-heavy documents, Commercial is clearly better. Cloud and Local run
  the same 8B model: rarely wrong, but often imprecise, so a person should check what they produce. Cloud's
  value over Local here is mainly a more reliable reviewer.
- **Cost is concentrated in math-heavy documents.** Ordinary documents trigger nothing. On this paper
  Commercial cost $0.96, more than half of it the "review every figure" pass over 150 formulas. Without that
  option, expect roughly $0.45.
- **Recommendation:** use Commercial for math-heavy documents where accuracy matters, or Cloud plus a person
  checking the flagged readings. Keep Local for documents that must stay on our own servers.

## Limits

- One document. Chemistry, medical and engineering notation weren't part of this test; real documents for
  them are a follow-up (issue #20).
- Not tested: the region cap (`AI_NOTATION_MAX`, 100) isn't reached by this paper (82 regions), and the Adobe
  route doesn't use the notation fallback yet. Both are in issue #20.
- Accuracy is judged by an AI reviewer, not by a person who reads math. A small human-scored sample would
  make the percentages firmer.
- Formulas are matched between runs by position, and fragment joining differs a little between runs, so
  only a few formulas could be paired exactly for side-by-side examples.

## Reproducing it

1. In the portal, sign in as a user with the Cloud and Commercial AI options. Upload `2610.01711v1.pdf`
   twice, choosing each option, with **Review every figure with AI** ticked.
2. Each document's report lists the readings (`reports.ai_notation`) and the review findings ("read-aloud
   text written by AI"). The tag viewer shows each Formula's read-aloud text next to the page.
3. To score both options with the same reviewer, re-run the review of one job's main file with the other
   option's review model (`verify_output` with `only_elements` set to the AI-read formulas and
   `judge_model` set to the reviewer).

**Raw results (local stack):**

| Option | Portal job | Engine job |
|---|---|---|
| Cloud AI | `66f2de3f-8331-4bf3-9d40-7586bd133ef9` | `84770ab6-4552-4571-986b-7b30c2ac6304` |
| Commercial AI | `03a3d731-daa0-4aad-bec1-aef81bd99fa4` | `9db92fe9-e523-43f2-88a1-d4c65ee88c9d` |
| Local AI | `c71bc20c-0b1c-46f0-a2ba-bfbb8afb8f20` (before the fixes); the after-fix run was made outside the portal | – |

Total spent on this test: about $1.20, which includes $0.22 for re-scoring Cloud with Commercial's reviewer.
