# Design notes and next milestones

## Guiding principle

Spend **zero model tokens** to gather deterministic evidence. Spend model tokens only on the smallest high-signal diff or image that justifies semantic analysis. Use one model, once, and cache a stable result. Multi-agent collaboration is not free: fan-out often multiplies identical prompt/context tokens.

## Next steps after v0.3

1. **Login/session fixtures**: explicitly opt-in Playwright persistent auth state loaded from a private path; never collect credentials automatically.
2. **axe-core accessibility** and optional Google Lighthouse CLI (separate optional Node dependencies). Distinguish rule failures from design judgments.
3. **Stable responsive baseline**: animation masks, network-idle or app-specific ready selectors, deterministic browser/OS environment, configurable interaction steps.
4. **Exact tokenizer/usage telemetry**: capture model-provided billing counts where available and record cache hit/paid-call totals without promising a hard cap.
5. **GitHub/GitLab MR adapter**: only comment on changed lines, dedupe prior comments, no write permissions without explicit approval. Never post sensitive screenshots by default.
6. **Better repository context**: local tree-sitter parsers for PHP/TS/SQL function signatures and affected symbols, opt-in context expansion under budget.
7. **Live DOM annotations**: extension/CDP-assisted tagging so user clicks map to exact component/source location, with opt-in WebSocket feedback bridge and authentication.
8. **Pluggable providers**: local Ollama for draft triage; paid models for difficult findings. Compare quality/cost on a labeled benchmark rather than assuming "multi-agent" is automatically better.

## Why not blindly use Lavish AXI for app QA?

Lavish primarily annotates HTML artifacts. A complete live SPA QA tool additionally needs routing, authentication, browser errors, responsive checks, JS app runtime, and reproducible screenshot baselines. We integrate Lavish for report HTML without claiming it can monitor live app state.

## Known limitations

- Playwright runs a true browser but browser installation is separate. Custom executable fallback currently exists only for system Chromium.
- `--static-dir` is intentionally not a full development server. External assets may not render.
- Diff-only review ignores untracked files and the selected patch context can be incomplete.
- Regex credential masking can miss secrets in arbitrary syntax.
- Candidate CSS selectors use simple element tag/id/first-class heuristics; results may be ambiguous.
- Pixel change ratio can produce noise under differing fonts, anti-aliasing and OS/browser versions.
- Codex invocation is covered by command-level mocked tests; the Codex binary was not installed in the local test environment.

## Shipped in v0.3

- Opt-in PostgreSQL/MySQL SELECT-only metadata inspection with minimum-privilege credentials.
- Query-plan analysis (`EXPLAIN` default, `EXPLAIN ANALYZE` explicit opt-in), sanitized metrics, transaction ROLLBACK.
- Composer PHPUnit and PHPStan local runner, bounded output/timeout, no cloud call.
- HTTP API JSON contract smoke checks, environment bearer auth, disabled redirects and opt-in remote/mutating requests.
- Unit tests for new paths, plus local HTTP server tests (not a live database integration claim).
