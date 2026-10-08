"""Cross-platform, bounded, offline-oriented multi-language check orchestration.

Discovery never executes project code. Execution is explicit; check commands can
execute arbitrary code from a trusted repository and must use a test environment.
No dependency installation, package download, or shell invocation is performed here.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .core import redact

MONOREPO_DIRS = ("apps", "packages", "services", "libs")
SINGLE_DIRS = ("backend", "frontend", "server", "client", "api", "web")
MARKERS = (
    "composer.json", "package.json", "pyproject.toml", "requirements.txt",
    "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts",
    "settings.gradle", "settings.gradle.kts", "manage.py", "artisan",
)


def _read_limited(path: Path, max_bytes: int = 256_000) -> str:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > max_bytes:
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _json_file(path: Path) -> dict:
    try:
        doc = json.loads(_read_limited(path))
        return doc if isinstance(doc, dict) else {}
    except (ValueError, OSError):
        return {}


def _is_project(path: Path) -> bool:
    if any((path / item).is_file() for item in MARKERS):
        return True
    return any(p.suffix in {".csproj", ".sln", ".slnx"} for p in path.iterdir() if p.is_file())


def discover(root: Path, *, max_projects: int = 12) -> list[dict[str, Any]]:
    """Inspect root and bounded workspace folders. No recursion into dependencies."""
    if not 1 <= max_projects <= 50:
        raise ValueError("--max-projects must be in 1..50")
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("--repo must refer to an existing directory")
    candidates = [root]
    for name in SINGLE_DIRS:
        path = root / name
        if path.is_dir() and not path.is_symlink():
            candidates.append(path)
    for name in MONOREPO_DIRS:
        group = root / name
        if group.is_dir() and not group.is_symlink():
            for candidate in sorted(group.iterdir(), key=lambda p: p.name.lower()):
                if candidate.is_dir() and not candidate.is_symlink() and not candidate.name.startswith("."):
                    candidates.append(candidate)
    found = []
    for project in candidates:
        if len(found) >= max_projects:
            break
        if not _is_project(project):
            continue
        langs: set[str] = set()
        frameworks: set[str] = set()
        manifests: list[str] = []
        package = _json_file(project / "package.json")
        composer = _json_file(project / "composer.json")
        if (project / "package.json").is_file():
            langs.add("node")
            manifests.append("package.json")
            normal_deps = package.get("dependencies", {})
            dev_deps = package.get("devDependencies", {})
            deps = {**(normal_deps if isinstance(normal_deps, dict) else {}),
                    **(dev_deps if isinstance(dev_deps, dict) else {})}
            for key, label in (("@angular/core", "angular"), ("react", "react"),
                               ("vue", "vue"), ("next", "nextjs"), ("@nestjs/core", "nestjs"),
                               ("express", "express"), ("nx", "nx")):
                if key in deps:
                    frameworks.add(label)
            if (project / "tsconfig.json").is_file() or "typescript" in deps:
                langs.add("typescript")
        if (project / "composer.json").is_file() or (project / "artisan").is_file():
            langs.add("php")
            manifests.append("composer.json" if (project / "composer.json").is_file() else "artisan")
            required = composer.get("require", {})
            dev = composer.get("require-dev", {})
            deps = {**(required if isinstance(required, dict) else {}),
                    **(dev if isinstance(dev, dict) else {})}
            if "laravel/framework" in deps or (project / "artisan").is_file():
                frameworks.add("laravel")
            if "symfony/framework-bundle" in deps:
                frameworks.add("symfony")
        if any((project / name).is_file() for name in ("pyproject.toml", "requirements.txt", "manage.py")):
            langs.add("python")
            manifests.append("pyproject.toml" if (project / "pyproject.toml").is_file() else "requirements.txt")
            py_text = (_read_limited(project / "pyproject.toml") + "\n" +
                       _read_limited(project / "requirements.txt")).lower()
            for name in ("django", "fastapi", "flask"):
                if re.search(r"\b" + name + r"\b", py_text):
                    frameworks.add(name)
            if (project / "manage.py").is_file():
                frameworks.add("django")
        if (project / "go.mod").is_file():
            langs.add("go")
            manifests.append("go.mod")
            mod = _read_limited(project / "go.mod").lower()
            if "github.com/gin-gonic/gin" in mod:
                frameworks.add("gin")
            if "github.com/gofiber/fiber" in mod:
                frameworks.add("fiber")
        if (project / "Cargo.toml").is_file():
            langs.add("rust")
            manifests.append("Cargo.toml")
        if (project / "pom.xml").is_file() or any((project / name).is_file() for name in
                                                     ("build.gradle", "build.gradle.kts")):
            langs.add("java")
            manifests.append("pom.xml" if (project / "pom.xml").is_file() else "build.gradle")
            java_manifest = "\n".join(_read_limited(project / x) for x in
                                      ("pom.xml", "build.gradle", "build.gradle.kts"))
            if "spring-boot" in java_manifest or "org.springframework.boot" in java_manifest:
                frameworks.add("spring-boot")
        if any(p.suffix in {".sln", ".slnx", ".csproj"} for p in project.iterdir() if p.is_file()):
            langs.add("dotnet")
            manifests.append("*.sln / *.csproj")
            if any("Microsoft.NET.Sdk.Web" in _read_limited(p)
                   for p in project.iterdir() if p.is_file() and p.suffix == ".csproj"):
                frameworks.add("aspnet-core")
        found.append({"path": "." if project == root else project.relative_to(root).as_posix(),
                      "languages": sorted(langs), "frameworks": sorted(frameworks),
                      "manifests": manifests, "_package": package})
    return found


def _command(binary: str) -> str | None:
    return shutil.which(binary)


def _local_bin(repo: Path, name: str) -> str | None:
    for directory in (repo / ".venv" / "Scripts", repo / ".venv" / "bin"):
        for ext in ((".exe", ".cmd", ".bat", "") if os.name == "nt" else ("",)):
            path = directory / (name + ext)
            if path.is_file():
                return str(path)
    return _command(name)


def _node_manager(repo: Path) -> str:
    if (repo / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (repo / "yarn.lock").is_file():
        return "yarn"
    if (repo / "bun.lock").is_file() or (repo / "bun.lockb").is_file():
        return "bun"
    return "npm"


def plan_checks(root: Path, projects: list[dict]) -> list[dict]:
    """Return runnable/skipped checks; never run tools during plan generation."""
    planned: list[dict] = []

    def add(path: str, tool: str, kind: str, cmd: list[str] | None, reason: str = "") -> None:
        planned.append({"project": path, "tool": tool, "kind": kind,
                        "status": "planned" if cmd else "skipped", "command": cmd,
                        **({"reason": reason or "Required local tool is unavailable"} if not cmd else {})})

    for project in projects:
        rel = project["path"]
        folder = root if rel == "." else root / rel
        langs = project["languages"]
        if "php" in langs:
            from .backend import discover_tools
            tools = discover_tools(folder)
            add(rel, "phpunit", "test", [*tools["phpunit"], "--no-coverage", "--colors=never"]
                if tools["phpunit"] else None, "Install project-local vendor/bin/phpunit and PHP")
            args = ["analyse", "--no-progress", "--error-format=raw"]
            config = any((folder / c).is_file() for c in ("phpstan.neon", "phpstan.neon.dist", "phpstan.dist.neon"))
            if not config:
                target = "app" if (folder / "app").is_dir() else "src" if (folder / "src").is_dir() else None
                if target:
                    args += ["--level=5", target]
            add(rel, "phpstan", "lint", [*tools["phpstan"], *args] if tools["phpstan"] and (config or target) else None,
                "Install project-local PHPStan and supply phpstan.neon or app/src")
        if "node" in langs:
            scripts = project["_package"].get("scripts", {})
            if not isinstance(scripts, dict):
                scripts = {}
            manager = _node_manager(folder)
            executable = _command(manager)
            for kind, candidates in (("lint", ("lint",)),
                                     ("typecheck", ("typecheck", "type-check", "check-types")),
                                     ("test", ("test:ci", "test:unit", "test"))):
                script = next((s for s in candidates if isinstance(scripts.get(s), str) and scripts[s].strip()), None)
                if script:
                    add(rel, f"{manager}:{script}", kind,
                        [executable, "run", script] if executable else None,
                        f"{manager} unavailable; install it and project dependencies")
                elif kind == "typecheck" and (folder / "tsconfig.json").is_file():
                    local = folder / "node_modules" / ".bin" / ("tsc.cmd" if os.name == "nt" else "tsc")
                    add(rel, "tsc", "typecheck", [str(local), "--noEmit"] if local.is_file() else None,
                        "No typecheck script or local node_modules/.bin/tsc")
                else:
                    add(rel, f"node:{kind}", kind, None, f"No project script configured for {kind}")
        if "python" in langs:
            for tool, kind, args in (("ruff", "lint", ["check", "."]),
                                     ("pytest", "test", ["-q"]),
                                     ("mypy", "typecheck", ["."])):
                binary = _local_bin(folder, tool)
                add(rel, tool, kind, [binary, *args] if binary else None,
                    f"{tool} not available in project virtualenv or PATH")
        if "go" in langs:
            binary = _command("go")
            add(rel, "go-test", "test", [binary, "test", "./..."] if binary else None)
            add(rel, "go-vet", "lint", [binary, "vet", "./..."] if binary else None)
        if "rust" in langs:
            binary = _command("cargo")
            add(rel, "cargo-test", "test", [binary, "test", "--offline", "--locked"] if binary else None)
            add(rel, "cargo-clippy", "lint", [binary, "clippy", "--offline", "--locked", "--all-targets", "--", "-D", "warnings"] if binary else None)
        if "java" in langs:
            maven = (folder / "pom.xml").is_file()
            tool = "mvn" if maven else "gradle"
            binary = _command(tool)
            cmd = ([binary, "--offline", "--batch-mode", "--no-transfer-progress", "test"] if maven else
                   [binary, "--offline", "--no-daemon", "test"]) if binary else None
            add(rel, tool + "-test", "test", cmd,
                f"{tool} not in PATH; wrappers are not auto-executed to avoid downloads")
        if "dotnet" in langs:
            binary = _command("dotnet")
            add(rel, "dotnet-test", "test", [binary, "test", "--no-restore"] if binary else None)
    return planned


def run_checks(root: Path, *, timeout: int = 120, output_limit: int = 4000,
               max_projects: int = 12, kind: str = "all", execute: bool = False) -> dict:
    if not 1 <= timeout <= 900 or not 200 <= output_limit <= 50000:
        raise ValueError("--timeout must be 1..900 and --output-limit 200..50000")
    if kind not in {"all", "lint", "test", "typecheck"}:
        raise ValueError("Unknown check kind")
    root = root.resolve()
    projects = discover(root, max_projects=max_projects)
    plan = [check for check in plan_checks(root, projects) if kind == "all" or check["kind"] == kind]
    for project in projects:
        project.pop("_package", None)
    results = []
    for item in plan:
        check = dict(item)
        if check["status"] == "planned" and execute:
            env = os.environ.copy()
            env["CI"] = "true"  # discourage watchers; scripts may ignore it
            env["PIP_NO_INDEX"] = "1"
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env["GOPROXY"] = "off"
            env["GOSUMDB"] = "off"
            env["GOTOOLCHAIN"] = "local"
            env["GOFLAGS"] = "-mod=readonly"
            env["RUSTUP_AUTO_INSTALL"] = "0"
            env["DOTNET_SKIP_FIRST_TIME_EXPERIENCE"] = "1"
            directory = root if item["project"] == "." else root / item["project"]
            start = time.monotonic()
            try:
                proc = subprocess.run(item["command"], cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                      capture_output=True, text=True, encoding="utf-8", errors="replace",
                                      timeout=timeout, shell=False)
                check["status"] = "passed" if proc.returncode == 0 else "failed"
                check["exit_code"] = proc.returncode
                check["output_tail"] = redact((proc.stdout + "\n" + proc.stderr)[-output_limit:])
            except subprocess.TimeoutExpired:
                check["status"] = "timeout"
            except OSError as exc:
                check["status"] = "error"
                check["reason"] = "Could not start checker (" + type(exc).__name__ + ")"
            check["duration_ms"] = int((time.monotonic() - start) * 1000)
        results.append(check)
    executed = sum(x["status"] in {"passed", "failed", "error", "timeout"} for x in results)
    return {"repo": str(root), "projects": projects, "checks": results,
            "counts": {state: sum(x["status"] == state for x in results)
                       for state in ("planned", "passed", "failed", "error", "timeout", "skipped")},
            "executed": executed, "token_cost": 0,
            "note": ("Planning only; no project code executed." if not execute else
                     "Local project checks were executed, potentially with side effects. "
                     "Use a trusted repository and test DB. Checks are not sandboxed; package scripts may access network.")}
