# News Alpha

A pre-registered test of whether an AI model reading SEC 8-K filings can beat the Russell 2000 (ETF IWM). The test ended in a no-go, and the point of the project is the method, not the performance.

## Why

Backtests of "AI trading signals" are easy to fool: the rules get adjusted after the results are seen, the data contain survivors only, or the model has already read the period it is tested on. The question here was narrow and the answer had to be honest whichever way it fell:

> Does Claude Haiku 4.5, reading the 8-K filings of US-listed companies worth $300m to $10bn, produce a signal that beats IWM after trading costs?

The success criteria were frozen before any result existed, and the project was set up so that a negative answer would be accepted as a result and not tuned away.

## What it does

- Collects 8-K filings from SEC EDGAR for a date range and filters the universe (listed common stock, size, price and liquidity limits, no SPACs or BDCs).
- Sends each filing to the model once, with a fixed prompt, and validates the JSON answer against a strict schema. Invalid answers are rejected and counted, never repaired.
- Turns the answer into a score, computes the return from the next market open at 1, 5 and 20 sessions, and subtracts the IWM return.
- Stores every step in SQLite and writes a Markdown report per run.
- Applies the frozen decision rule (monthly top-quintile return net of a 1.2% cost, Newey-West t-statistic, months positive) and writes the verdict.

## How it was built

Antoine Voinchet designed and directed the project: the question, the pre-registered rules, the go/no-go criteria, the spending ceilings and each deviation from the plan. The code was written by Claude Code under that direction. All 88 commits of the working repository carry a Claude co-author trailer.

The method is what he contributed, and it is visible in the repository:

- **Rules before data.** `SPEC.md` was frozen on 25 October 2026 with the hypothesis, the filters, the statistics and the thresholds. A cap of two documented iterations was set, allowed only on criteria that ignore returns.
- **Frozen files.** The prompt and the analysis module are pinned by SHA-256 in `SPEC.md`. The code refuses to run if either has changed.
- **Bias guards.** The test window starts after the model's training data end; listing and industry code are read at the date of each filing (delisted companies are kept); no fallback to a thinner price feed; the last three months were held out and locked in code.
- **Independent review and tests.** An independent review of the preparation led to corrections before the campaign. The suite has 167 tests, plus mutation tests that break 48 critical rules one at a time and check that the suite notices.

Details and sources: [docs/METHOD.md](docs/METHOD.md).

## Results and status

Verdict: **NO-GO** (`reports/phase1_analysis.md`, generated 28 October 2026).

- 16,432 filings in the analysed population, 15,907 events, September 2025 to June 2026.
- Top quintile at 20 sessions, net of the 1.2% cost: -1.26% on average per month, Newey-West t of -2.73, 3 months positive out of 10. The thresholds were above zero, above 2, and at least 7 of 10.
- Before costs the top quintile was close to zero (-0.06% winsorised, +0.25% raw), and the gross gap between the best and worst scored filings at 20 sessions was not statistically significant (Newey-West t of 0.79).

What this does not prove:

- It covers one model, one prompt and one period. It does not show that no AI model can read filings usefully.
- The 1.2% cost is an assumption fixed in advance, not a measured execution cost.
- The first test was deliberately simple: minimal prompt, a fast model without extended reasoning, daily prices, and no power calculation before launch.
- The held-out sample (July to September 2026) was not processed, because the no-go made it unnecessary. The result was not confirmed out of sample.

This is a research prototype, not a trading system. It places no orders and is not in production.

## How to run

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```
uv sync --frozen
uv run --frozen pytest
```

The tests use fictitious data and need no network or API key. In a fresh copy of this repository, 167 tests passed in about 11 seconds, with the API variables unset. The mutation tests (`uv run --frozen python -I tests/mutations.py`) caught all 48 mutations in that copy.

To run the pipeline, copy `.env.example` to `.env` and fill `ANTHROPIC_API_KEY`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` (Alpaca market data, SIP feed) and `SEC_USER_AGENT` (the SEC asks for a name and a contact address). The `.env` file is ignored by git.

```
uv run --frozen python -m news_alpha.run --start 2025-09-02 --end 2025-09-15 --limit 20
```

`--limit` draws that many eligible filings at random with a fixed seed (`0` takes all of them). `--max-calls` caps model calls per run (50 by default). Each run writes `reports/run_<start>_<end>.md` and fills `data/news_alpha.sqlite`. Re-running does not call the model again for a filing already scored with the same prompt and model. The pipeline commands call paid APIs and were not run for this publication; only `--help` was checked.

The `data/` folder (database and cache of EDGAR and Alpaca responses) is not published. To rebuild it, run the command above month by month with `--limit 0` and a higher `--max-calls`, over 2025-09-01 to 2026-06-30. This spends model credits, and whether Claude Haiku 4.5 is still available has not been checked.

`uv run --frozen python -m news_alpha.analysis` writes the phase 1 report once, and refuses to overwrite it. The published report is already in `reports/`, so it will refuse here.

## Repository layout

```
SPEC.md            frozen protocol and decision criteria (French)
docs/METHOD.md     summary of the method and the verdict (English)
src/news_alpha/    pipeline: ingest, filters, extract, score, prices, returns, analysis, run
prompts/           frozen prompt v2 (phase 1) and archived v1 (phase 0)
tests/             unit tests and mutation tests
reports/           phase 1 verdict and phase 0 sample (French)
```

The specification, the reports and the code comments are in French, the language the project was worked in.

## Licence

Copyright (c) 2026 Antoine Voinchet. All rights reserved. See `LICENSE`.
