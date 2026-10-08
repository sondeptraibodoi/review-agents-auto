# Lean Review Agents v0.3.0

**Cross-platform, local-first reviewer** for Git changes, PHP/Laravel backend checks, PostgreSQL/MySQL metadata/query plans, HTTP API contracts and Playwright visual UI testing.

- Python **3.10+**, Windows / macOS / Linux. No Docker or cloud service is required.
- **Zero AI tokens by default.** `--ai` and `ui ai` opt into Codex when installed. Local database metadata, CLI tests and API probes never call a model.
- Nothing is automatically pushed to GitHub/GitLab; the tool does not run migrations, write DB rows, auto-fix code or post PR comments.
- **Not production-certified.** Offline tests, simulated DB-driver tests and a local HTTP server test pass. Live PostgreSQL/MySQL connections, Codex, Windows/macOS CI runs have not been independently validated here.

## Install

From extracted repository root:

```bash
python -m pip install -e '.[all]'
python -m playwright install chromium
leanreview doctor
```

PowerShell:

```powershell
py -m pip install -e ".[all]"
py -m playwright install chromium
leanreview doctor
```

Install only needed pieces to keep it light:

| Extra | Dependencies | Features |
| --- | --- | --- |
| Base: `pip install -e .` | standard library | Git diff review, local PHP runners, HTTP API checks |
| `.[postgres]` | psycopg3 | PostgreSQL |
| `.[mysql]` | PyMySQL | MySQL |
| `.[db]` | both drivers | PostgreSQL + MySQL |
| `.[ui]` | Playwright + Pillow | UI audit, screenshots |
| `.[all]` | all optional | Everything |

PHP checks need your PHP project to have installed Composer dependencies, `php` in PATH and `vendor/bin/phpunit` / `vendor/bin/phpstan`. No Composer installation is performed by LeanReview.

## 1. Code review (Git diff)

```bash
cd /path/to/your-repository
leanreview review --base origin/main --max-tokens 2500 --max-files 20
leanreview review --staged
leanreview review --base origin/main --ai        # optional Codex call
```

- Offline checks are heuristic hints only; `--max-tokens` caps the approximate selected patch, **not** total Codex billed usage.
- AI is opt-in, read-only and cached by content/model. See `leanreview review --help`.

## 2. Backend: PHPUnit + PHPStan

```bash
cd /path/to/laravel-project
leanreview backend check                 # Auto-detect installed tools
leanreview backend check --only phpunit
leanreview backend check --only phpstan --timeout 180
leanreview backend check --output .leanreview/backend.json
```

- Discovers **project-local Composer executables** only, and runs using `php` without `shell=True`.
- Executes PHPUnit (`--no-coverage --colors=never`) and PHPStan (`analyse --no-progress --error-format=raw`).
- Without a PHPStan config, runs `--level=5` on `app/` or `src/`; otherwise obeys project PHPStan config.
- Per-command timeouts, bounded console output; failed tests return exit code 1, missing tools without any executed check return code 2.
- **Caution:** PHPUnit executes arbitrary project test code and may write records, trigger emails/jobs, or connect to production if `.env.testing`/mock DB is misconfigured. Use a dedicated test environment.

## 3. Connect PostgreSQL and MySQL (read-only)

Set the connection URL in the process environment **outside source control**:

```bash
# bash/zsh example, localhost uses local non-TLS default
export LEANREVIEW_DATABASE_URL='postgresql://review_ro:CHANGE_ME@127.0.0.1:5432/myapp'
# or
export LEANREVIEW_DATABASE_URL='mysql://review_ro:CHANGE_ME@127.0.0.1:3306/myapp'
```

PowerShell example:

```powershell
$env:LEANREVIEW_DATABASE_URL = 'postgresql://review_ro:CHANGE_ME@127.0.0.1:5432/myapp'
```

**Do not paste real credentials in Git, screenshots, shared shell scripts or CI logs.** URL-encode special characters in passwords (e.g. `@` → `%40`). For remote database hosts, default TLS is `verify-full`, requiring a trusted certificate. The unsafe override `--allow-insecure-db` should be limited to controlled development networks.

```bash
leanreview db inspect --limit 250
leanreview db inspect --tables fleet_route_monitoring fleet_good_delivery_receipts
leanreview db inspect --tables public.orders --output .leanreview/db-schema.json
```

Schema inspection reads **names/types/nullability, foreign keys, index definitions, tables** and **does not SELECT business rows**. Every metadata category has a row cap; if a large schema reaches the cap, increase `--limit` or use `--tables`.

### Recommend DB credentials with SELECT-only permissions

In PostgreSQL, as an administrator (adjust schema/database):

```sql
CREATE ROLE review_ro LOGIN PASSWORD 'USE_A_STRONG_PASSWORD';
GRANT CONNECT ON DATABASE myapp TO review_ro;
GRANT USAGE ON SCHEMA public TO review_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO review_ro;
-- Optional if your DBA wants SELECT privilege on new tables as well:
-- ALTER DEFAULT PRIVILEGES FOR ROLE schema_owner IN SCHEMA public GRANT SELECT ON TABLES TO review_ro;
```

In MySQL, as an administrator (restrict user host to a known address on real deployments):

```sql
CREATE USER 'review_ro'@'localhost' IDENTIFIED BY 'USE_A_STRONG_PASSWORD';
GRANT SELECT ON myapp.* TO 'review_ro'@'localhost';
```

**A SELECT-only account with minimal privileges is mandatory for safe use.** A read-only transaction + SQL text filter is only defense-in-depth, not a complete sandbox: SELECT can call functions with side effects, and `EXPLAIN ANALYZE` actually executes the statement.

## 4. Query analysis: EXPLAIN / EXPLAIN ANALYZE

Put one **SELECT** into a local `.sql` file (example: `examples/query.sql`):

```bash
leanreview db explain --sql-file examples/query.sql
leanreview db explain --sql-file examples/query.sql --analyze
leanreview db explain --sql-file examples/query.sql --timeout-ms 10000 --output .leanreview/query-plan.json
```

- Default is normal EXPLAIN: generates a plan **without executing the SELECT**.
- `--analyze` actually executes SELECT, collecting actual timing/row counts. It is **explicit opt-in** and should only target low-risk queries on a development/replica database. It can be expensive, acquire locks, invoke functions and impact performance despite ROLLBACK.
- Single SELECT only; CTEs, comments, multi-statements, modifications, locks and dangerous functions are rejected by a deliberately conservative *best-effort* filter.
- Always uses `BEGIN READ ONLY` (PostgreSQL) or `START TRANSACTION READ ONLY` (MySQL), sets query time limits and sends an explicit ROLLBACK.
- PostgreSQL: extracts scan nodes, index nodes, estimates vs actual row counts, flags sequential scans and large estimate discrepancies. MySQL: `FORMAT=JSON` identifies full scans/keys; `ANALYZE` produces a truncated, literal-masked TREE plan.
- Reports include a SQL SHA-256 digest instead of the raw query. Reports can still expose database object names and should stay private.

## 5. HTTP API smoke/contract checks

Create a JSON contract (edit example to match your API):

```json
{
  "endpoints": [
    {"path": "/api/v1/health", "method": "GET", "expected_status": 200, "required_keys": ["status"], "max_ms": 3000},
    {"path": "/api/v1/me", "method": "GET", "expected_status": 200, "required_keys": ["data"]}
  ]
}
```

Run it against a local running Laravel server:

```bash
leanreview api check --url http://127.0.0.1:8000 --contract examples/api-contract.json
leanreview api check --url http://localhost:8000 --contract examples/api-contract.json --bearer-env TEST_API_TOKEN
leanreview api check --url http://localhost:8000 --contract examples/api-contract.json --output .leanreview/api.json
```

PowerShell bearer example: `$env:TEST_API_TOKEN = 'YOUR_TEST_TOKEN'` (prefer a secret store/interactive input over saving this in scripts).

- Compares status codes, checks JSON nested key paths (e.g. `data.id`), checks latency thresholds, timeouts, maximum response size and reports failures.
- **GET/HEAD only** by default. POST/PUT/PATCH/DELETE require `--allow-mutation`. Remote URL requires `--allow-remote`. Redirects are **blocked**.
- Auth tokens are injected from environment variables. **No response bodies or authorization headers** are written to reports. APIs can still have unexpected side effects, even for GET; point at staging/test data.
- This is a smoke/contract tester, **not** an automatic security fuzzer, OpenAPI parser or comprehensive performance load tester.

## 6. Review a running frontend

```bash
leanreview ui audit --url http://localhost:4200 --paths / /login /dashboard --widths 390 768 1440
leanreview ui lavish .leanreview/ui/review.html
leanreview ui ai --image .leanreview/ui/<screenshot>.png   # opt-in AI; image charges apply
```

Screenshots, mobile overflow, console errors, broken images and a local feedback HTML UI work as in v0.2.0.

## Security / privacy summary

| Operation | Calls AI? | Reads database rows? | Writes DB? | Requires separate opt-in? |
|---|---|---|---|---|
| `review` | No | No | No | AI optional `--ai` |
| `backend check` | No | Test-dependent | Test-dependent | Command explicit |
| `db inspect` | No | No | No | DB URL required |
| `db explain` | No | Only with `--analyze` | Not by tool; functions could have side effects | `--analyze` |
| `api check` | No | HTTP response inspected in memory | API-dependent | Non-GET `--allow-mutation` |
| `ui audit` | No | No | No | Remote `--allow-remote` |

## Testing

```bash
python -m unittest discover -s tests -v
```

CI config covers Windows, macOS and Linux with Python 3.10/3.13 plus browser smoke tests. DB connection behavior is verified with fake drivers; **real PostgreSQL/MySQL integration tests require separately provisioned non-production databases** and are not included in the default test suite.

## Roadmap

- Live DB integration tests with disposable containers (PostgreSQL 15+, MySQL 8.0+)
- Optional SQL params, sanitized EXPLAIN comparisons for before/after index changes
- OpenAPI import, authenticated API workflow tests and request/response JSON Schema validation
- Laravel-aware static analysis and small-code-context extraction (no raw rows sent to AI)
- GitLab/GitHub MR comments, local model selection, measured AI token usage
