### Finding 1

Status: resolved

The official live path must not bypass Slice 3 replay semantics. The implementation will write prediction JSONL and call the official replay adapter so hidden oracle ordering remains centralized.

### Finding 2

Status: resolved

Generator prompts are the highest-risk leak surface. The plan requires a public-only generator input and scanner check before either generator is invoked.

### Finding 3

Status: resolved

Budget evidence must fail closed. A cost overrun returns an unavailable report with false report-only switches, not a partial accepted benchmark result.

### Finding 4

Status: resolved

Matched-arm claims require identical generation configuration. The report must record model, provider, budget, timeout, and prompt hash for both arms.

## Waivers

No findings are waived. Docker-scale official oracle execution remains injectable because CI should not fetch or run the benchmark by default.
