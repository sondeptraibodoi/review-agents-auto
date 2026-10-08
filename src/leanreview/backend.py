"""Local PHPUnit/PHPStan runners. No shell, no installation, no model calls."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from .core import redact


def discover_tools(repo: Path) -> dict[str, list[str] | None]:
    repo = repo.resolve()
    php = shutil.which("php")
    found = {}
    for tool in ("phpunit", "phpstan"):
        # Only execute project-local Composer-installed scripts; never pull packages.
        script = repo / "vendor" / "bin" / tool
        win = repo / "vendor" / "bin" / (tool + ".bat")
        if script.is_file() and php:
            found[tool] = [php, str(script)]
        elif os.name == "nt" and win.is_file():
            found[tool] = [str(win)]
        else:
            found[tool] = None
    return found


def _run(command: list[str], cwd: Path, timeout: int, max_chars: int) -> dict:
    start = time.monotonic()
    try:
        p = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           shell=False, stdin=subprocess.DEVNULL)
        status = "passed" if p.returncode == 0 else "failed"
        # Cap before formatting. Logs can still contain personal data: stored locally only.
        output = redact((p.stdout + "\n" + p.stderr)[-max_chars:])
        return {"status": status, "exit_code": p.returncode, "duration_ms": round((time.monotonic() - start) * 1000),
                "output_tail": output}
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "duration_ms": round((time.monotonic() - start) * 1000),
                "message": f"Exceeded {timeout}s timeout"}
    except OSError as exc:
        return {"status": "error", "message": "Could not start runner: " + type(exc).__name__}


def backend_check(repo: Path, *, only: str = "all", timeout: int = 120,
                  output_limit: int = 6000) -> dict:
    if only not in {"all", "phpunit", "phpstan"}:
        raise ValueError("--only must be all/phpunit/phpstan")
    if not 1 <= timeout <= 900 or not 200 <= output_limit <= 50000:
        raise ValueError("--timeout must be 1..900 and --output-limit 200..50000")
    repo = repo.resolve()
    if not repo.is_dir():
        raise ValueError("--repo must refer to a directory")
    available = discover_tools(repo)
    tools = [only] if only != "all" else ["phpunit", "phpstan"]
    runs = {}
    for tool in tools:
        prefix = available[tool]
        if not prefix:
            runs[tool] = {"status": "skipped", "reason": "Composer tool not installed or PHP missing"}
            continue
        args = (["--no-coverage", "--colors=never"] if tool == "phpunit" else
                ["analyse", "--no-progress", "--error-format=raw"])
        # PHPStan without config may need 'app' path (Laravel) or src.
        # Do not guess project-wide settings for projects without config.
        if tool == "phpstan" and not any((repo / n).is_file() for n in
                                         ("phpstan.neon", "phpstan.neon.dist", "phpstan.dist.neon")):
            target = "app" if (repo / "app").is_dir() else "src" if (repo / "src").is_dir() else None
            if target:
                args += ["--level=5", target]
            else:
                runs[tool] = {"status": "skipped", "reason": "No PHPStan config or app/src directory"}
                continue
        runs[tool] = _run([*prefix, *args], repo, timeout, output_limit)
    return {"repo": str(repo), "runs": runs, "token_cost": 0,
            "note": "Executes local project test code. Use an isolated/test environment; tests may write data."}
