# v0.4 architecture and boundaries

`leanreview detect` reads only bounded local manifest metadata. `leanreview check` produces a plan. `leanreview check --run` and `leanreview review --all` execute selected commands via subprocess with an explicit working directory and deadline.

No package manager install or external AI invocation is required for these commands. The program checks for installed tool binaries and emits `skipped` if missing. A test script may independently access the internet or mutate project databases; **only execute trusted project scripts on disposable/test infrastructure**.

For code review, `core.py` selects and prioritizes a redacted Git patch. `ai.py` runs a single optional Codex session inside an isolated temporary directory, and caches structured output by patch hash/model. The AI review does not automatically receive file contents, local test output, database content, screenshots or API response bodies. The default remains zero AI tokens.

An Nx / pnpm / Yarn monorepo with nested project scripts is supported at the manifest level to one workspace layer. Deep recursive projects and automatic per-project Nx targeting are not implemented. Local checks currently run sequentially, in discovery order, to avoid multiplying CPU/RAM use and provide deterministic logs.

## Check capabilities and limitations

- Node: chooses configured `lint`, `typecheck`/`type-check`/`check-types`, `test:ci`/`test:unit`/`test` scripts; honors pnpm/yarn/bun lockfiles and skips missing package managers instead of silently switching.
- Python: invokes available Ruff, pytest and Mypy, preferring local venv executables. If tools are global or absent, results reflect that; no environment auto-created.
- PHP: checks vendor-local PHPUnit/PHPStan. Missing vendor dependencies -> skipped.
- Go: `GOPROXY=off`, `GOFLAGS=-mod=readonly`, `GOTOOLCHAIN=local` applied to avoid downloading modules/toolchains; missing cached dependencies fail rather than downloading.
- Rust: Cargo offline/locked, rustup auto-install disabled. Java: Maven/Gradle offline. .NET: `--no-restore`.
- All tools: 120 second default per check, output tail max 4000 chars. Limits configurable. No automatic code modifications from the reviewer, but project scripts might write files.

## Not yet implemented

Multi-model routing across Ollama/Claude/Gemini, AST semantic cross-file tracing, coverage-aware test selection, in-sandbox running of untrusted code, and GitHub/GitLab inline MR comments. These require separate development and evaluation rather than being claimed based on basic CLI support.
