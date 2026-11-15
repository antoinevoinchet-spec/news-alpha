# Method: what was fixed before the data, and what the verdict covers

This note summarises the protocol, the decision criteria and the verdict of the phase 1 test. It is written in English from the French documents kept in this repository: `SPEC.md` (the protocol, v0.3, 25 October 2026) and `reports/phase1_analysis.md` (the phase 1 report, generated 28 October 2026). Both are in French and are the sources of every statement below.

## The question

Can an AI model that reads SEC Form 8-K filings of US-listed companies (market capitalisation $300m to $10bn) pick out announcements that the market under-reads, well enough to beat the Russell 2000 (ETF IWM) after trading costs? (`SPEC.md` section 2, section 4.)

The null hypothesis, H0, is the default: the abnormal return of the best-scored fifth of filings, net of costs, is zero or negative. The data have to say otherwise.

## What was fixed before the first result

`SPEC.md` was frozen as v0.3 on 25 October 2026, before any phase 1 result existed. It fixes:

- **Universe and filters**: common stock on NYSE, Nasdaq or NYSE American, read from the cover page of the filing itself, so that companies delisted since are kept; SPACs and BDCs excluded; price above $2; average 20-day traded value above $500k (section 5).
- **Model and prompt**: Claude Haiku 4.5 (`claude-haiku-4-5-20251001`), one call per filing, temperature 0, no tools, a JSON answer validated against a strict schema (section 7). A non-conforming answer is rejected and logged, never repaired.
- **Score**: direction x magnitude x confidence x (1 - share already priced in).
- **Entry and returns**: buy at the first market open that follows both the filing and the end of scoring, never at the close of the filing day; abnormal return is the stock minus IWM, at 1, 5 and 20 sessions (section 5).
- **Window**: filings from 1 September 2025, because the model's training data run to July 2025 (section 6). Analysis period: September 2025 to June 2026 (10 months). A held-out sample, July to September 2026, was kept unread for confirmation.
- **Decision rule** (sections 8 and 9), all of it required:
  - at least 300 events;
  - each month, the mean 20-session abnormal return of the top quintile, less a 1.2% round-trip cost, averaged over the months, must be above zero;
  - the Newey-West t-statistic of that monthly series must be above 2;
  - at least 7 of the 10 months must be positive;
  - the held-out sample must confirm.
  Otherwise: stop. The protocol says "a clean negative result is a result".
- **Limit on tinkering**: at most two documented iterations of the prompt or filters, and only on criteria that ignore returns (schema rejection rate, a proven bug, missing data). A disappointing result is never a reason to iterate. The protocol records 0 of 2 used at the freeze.
- **Fingerprints**: the prompt (`prompts/score_8k_v2.md`) and the analysis module (`src/news_alpha/analysis.py`) are frozen by SHA-256, written in `SPEC.md`. The code refuses to run if either file differs. I checked that the two files in this repository match the values in `SPEC.md`.

Before the campaign, an independent review of the phase 1 preparation led to the v0.3 changes (window start, criterion, extra bias controls). Its report is not published here; the list of changes is in `SPEC.md`, section 13.

## How the rules were protected

- The analysis module was written and tested on fictitious data with known results before any real run.
- `tests/mutations.py` breaks 48 critical rules one at a time in a copy of the package and checks that the test suite fails each time. The first pass, on the 47 rules defined at that point, killed 39 of 47; a test was added for each survivor, and one more rule was added later. The decisions log (not published) records this. Run again in this copy for the publication, all 48 mutations were caught.
- The held-out sample is locked in code: the run refuses it before 29 October 2026 and until the phase 1 report exists.
- After the NO-GO, the held-out sample was deliberately not processed. The protocol only needed it to confirm a GO, and spending the last untouched months on a model with no future was judged wasteful.

## The verdict

NO-GO (`reports/phase1_analysis.md`). The population was 16,432 filings, grouped into 15,907 events (company by entry session).

| Criterion | Met |
|---|---|
| at least 300 events | yes |
| mean monthly net criterion above zero | no (-1.26%) |
| Newey-West t above 2 | no (-2.73) |
| at least 7 months positive out of 10 | no (3 of 10) |

Context from the same report, which is informative and outside the decision: for the top quintile at 20 sessions the mean abnormal return before costs is -0.06% (winsorised) or +0.25% (raw), with a median of -1.52%. The top-minus-bottom spread at 20 sessions, gross, is +0.47% with a Newey-West t of 0.79. The report gives all other horizons and cost assumptions.

## What the verdict does not show

- It applies to one model, one prompt and one period (September 2025 to June 2026). It does not show that no AI model can read filings usefully.
- The 1.2% round-trip cost is an assumption fixed in advance, not a measured execution cost. The gross figures are in the report so another cost assumption can be applied.
- Known limits of this first test: a minimal prompt, a fast model without extended reasoning, daily prices with entry at the next open (no intraday data), and statistical power that was not computed before launch.
- The held-out sample was not run, so the test was not confirmed or contradicted out of sample.

## What is not published, and why

- `data/` (SQLite database and cache of EDGAR and Alpaca responses): large, and rebuilt by the pipeline (see the README).
- The internal decisions log. `SPEC.md` and some code comments cite decision numbers (D1 to D38) from it. The ones that matter here are summarised above.
- A follow-up protocol for a newer model generation was written and frozen on 29 October 2026. It has no result, and its documents contain personal financial details, so it is not included.

## Where each source is in this repository

| Topic | File |
|---|---|
| Protocol, rules, decision criteria | `SPEC.md` |
| Phase 1 result and decision | `reports/phase1_analysis.md` |
| Phase 0 sample (20 filings, pipeline check) | `reports/phase0_sample.md` |
| Frozen prompt (phase 1) and the archived phase 0 prompt | `prompts/score_8k_v2.md`, `prompts/score_8k_v1.md` |
| Frozen analysis code | `src/news_alpha/analysis.py` |
| Tests, including the mutation tests | `tests/` |
