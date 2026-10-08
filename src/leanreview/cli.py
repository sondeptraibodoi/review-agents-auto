"""LeanReview CLI: diff-first code review + free browser UI inspection."""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from . import __version__
from .core import get_diff, slice_diff, offline_findings, output_report, cache_file, write_private_json
from .ai import codex_review


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="leanreview", description="Local-first, budget-aware code + UI review")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check available tools")
    r = sub.add_parser("review", help="Review Git unstaged/staged/branch diff")
    r.add_argument("--repo", type=Path, default=Path.cwd())
    r.add_argument("--base", help="Merge-base branch/commit (e.g. origin/main)")
    r.add_argument("--staged", action="store_true")
    r.add_argument("--max-tokens", type=int, default=5000, help="Estimated patch-only token cap")
    r.add_argument("--max-files", type=int, default=30)
    r.add_argument("--ai", action="store_true", help="Optional single Codex pass")
    r.add_argument("--ai-on", choices=["always", "warnings"], default="always",
                   help="When --ai: always call AI or only when offline rules flag something")
    r.add_argument("--model", help="Codex model")
    r.add_argument("--no-cache", action="store_true")
    r.add_argument("--format", choices=["text", "json"], default="text")
    r.add_argument("--output", type=Path)
    u = sub.add_parser("ui", help="UI audits, human annotation and optional Lavish integration")
    ui = u.add_subparsers(dest="ui_command", required=True)
    a = ui.add_parser("audit", help="Check a running web app at multiple viewport sizes")
    a.add_argument("--url", required=True, help="e.g. http://localhost:4200")
    a.add_argument("--paths", nargs="+", default=["/"], help="Routes to inspect: / /login /dashboard")
    a.add_argument("--widths", type=int, nargs="+", default=[390, 768, 1440])
    a.add_argument("--out", type=Path, default=Path(".leanreview/ui"))
    a.add_argument("--baseline", type=Path, help="Compare against prior baseline directory")
    a.add_argument("--update-baseline", action="store_true", help="Store current screenshots as baseline")
    a.add_argument("--browser", choices=["chromium", "firefox", "webkit"], default="chromium")
    a.add_argument("--allow-remote", action="store_true", help="Allow non-local URLs / assets")
    a.add_argument("--static-dir", type=Path, help="Serve static HTML/build output into browser without a server")
    a.add_argument("--timeout", type=int, default=20000, help="Navigation timeout in ms")
    a.add_argument("--fail-on", choices=["never", "errors", "any", "regression"], default="never")
    v = ui.add_parser("ai", help="Opt-in visual AI review of ONE screenshot (downscaled + cached)")
    v.add_argument("--image", type=Path, required=True)
    v.add_argument("--route", default="/")
    v.add_argument("--model")
    v.add_argument("--max-dimension", type=int, default=1024)
    v.add_argument("--no-cache", action="store_true")
    v.add_argument("--output", type=Path)
    l = ui.add_parser("lavish", help="Open a trusted local HTML file in Lavish AXI (npx)")
    l.add_argument("file", type=Path)
    f = ui.add_parser("feedback", help="Convert exported screenshot annotations to a concise review prompt")
    f.add_argument("--file", type=Path, required=True)
    f.add_argument("--audit", type=Path, help="Optional report.json to map screen index to route")
    f.add_argument("--max-chars", type=int, default=4000)
    f.add_argument("--output", type=Path)
    d = sub.add_parser("db", help="Read-only PostgreSQL/MySQL metadata and EXPLAIN")
    dbs = d.add_subparsers(dest="db_command", required=True)
    for mode in ("inspect", "explain"):
        q = dbs.add_parser(mode, help="Read schema metadata" if mode == "inspect" else "Explain one SELECT")
        q.add_argument("--timeout-ms", type=int, default=5000)
        q.add_argument("--allow-insecure-db", action="store_true", help="Allow remote DB without verified TLS (unsafe)")
        q.add_argument("--output", type=Path)
        if mode == "inspect":
            q.add_argument("--limit", type=int, default=250, help="Max metadata rows PER category")
            q.add_argument("--tables", nargs="*", help="Filter table names (e.g. users public.users)")
        else:
            q.add_argument("--sql-file", type=Path, required=True, help="File containing one SELECT query")
            q.add_argument("--analyze", action="store_true", help="Actually execute SELECT (opt in!)")
    b = sub.add_parser("backend", help="Run local Laravel/PHP quality checks")
    bsub = b.add_subparsers(dest="backend_command", required=True)
    bc = bsub.add_parser("check", help="Auto-run installed PHPUnit/PHPStan locally")
    bc.add_argument("--repo", type=Path, default=Path.cwd())
    bc.add_argument("--only", choices=["all", "phpunit", "phpstan"], default="all")
    bc.add_argument("--timeout", type=int, default=120)
    bc.add_argument("--output-limit", type=int, default=6000)
    bc.add_argument("--output", type=Path)
    ac = sub.add_parser("api", help="Review API over HTTP")
    acsub = ac.add_subparsers(dest="api_command", required=True)
    at = acsub.add_parser("check", help="Execute local smoke/contract checks")
    at.add_argument("--url", required=True, help="Base URL, localhost by default")
    at.add_argument("--contract", type=Path, required=True, help="JSON contract file")
    at.add_argument("--allow-remote", action="store_true")
    at.add_argument("--allow-mutation", action="store_true", help="Allow POST/PUT/PATCH/DELETE (side effects possible)")
    at.add_argument("--bearer-env", help="Environment variable holding Bearer token")
    at.add_argument("--timeout", type=float, default=5.0)
    at.add_argument("--output", type=Path)
    return p


def format_text(data: dict) -> str:
    lines = [f"LeanReview: {len(data['files_reviewed'])} files, ~{data['estimated_patch_tokens']} patch tokens"]
    if data["truncated"]:
        lines.append("WARNING: diff truncated or files skipped by budget; review is incomplete")
    if data["files_ignored"]:
        lines.append(f"Ignored files: {len(data['files_ignored'])}")
    for f in data["offline_findings"]:
        lines.append(f"[offline:{f['severity']}] {f['file']}:{f['line']} {f['message']}")
    if data["ai"]:
        lines.append("AI summary: " + str(data["ai"].get("summary", "")))
        for f in data["ai"].get("findings", []):
            lines.append(f"[AI:{f['severity']}] {f['file']}:{f['line']} {f['issue']} => {f['suggestion']}")
    if data["ai_cache_hit"]:
        lines.append("AI cache hit: reused locally cached result (no new model call)")
    if not data["offline_findings"] and not (data["ai"] and data["ai"].get("findings")):
        lines.append("No findings from enabled checks (not proof of correctness)")
    return "\n".join(lines) + "\n"


def review(args) -> int:
    if args.max_tokens < 50 or args.max_files < 1:
        raise ValueError("--max-tokens must be >= 50, --max-files >= 1")
    repo = args.repo.resolve()
    selected = slice_diff(get_diff(repo, args.base, args.staged), args.max_tokens * 4, args.max_files)
    offline = offline_findings(selected.patch)
    ai, cached = None, False
    if args.ai and selected.patch and (args.ai_on == "always" or offline):
        cp = cache_file(repo, selected.sha256, "codex", args.model or "default")
        if cp.is_file() and not args.no_cache:
            try:
                ai = json.loads(cp.read_text(encoding="utf-8"))
                cached = True
            except (OSError, ValueError):
                ai = None
        if ai is None:
            ai = codex_review(selected.patch, repo, args.model)
            write_private_json(cp, ai)
    data = output_report(selected, offline, ai, cached)
    txt = json.dumps(data, ensure_ascii=False, indent=2) + "\n" if args.format == "json" else format_text(data)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(txt, encoding="utf-8")
    print(txt, end="")
    return 0


def ui_cmd(args) -> int:
    from .ui import audit_ui, feedback_prompt
    if args.ui_command == "audit":
        result = audit_ui(args.url, args.paths, args.widths, args.out,
                          allow_remote=args.allow_remote, baseline=args.baseline,
                          update_baseline=args.update_baseline,
                          browser_name=args.browser, timeout_ms=args.timeout,
                          static_dir=args.static_dir)
        print(f"UI audit: {len(result['screens'])} screen(s), {result['total_findings']} findings")
        print(f"JSON: {args.out.resolve() / 'report.json'}")
        print(f"Visual feedback: {args.out.resolve() / 'review.html'}")
        for s in result["screens"]:
            print(f"  {s['route']} at {s['width']}px: {len(s['issues'])} issue(s), "
                  f"comparison={s.get('comparison', {}).get('status', 'not compared')}")
        if args.fail_on == "regression" and result["has_regression"]:
            return 1
        if args.fail_on == "any" and result["total_findings"]:
            return 1
        if args.fail_on == "errors" and any(
            f["severity"] == "error" for s in result["screens"] for f in s["issues"]
        ):
            return 1
        return 0
    if args.ui_command == "ai":
        from .vision import visual_review
        data, cache_hit, _ = visual_review(
            args.image.resolve(), args.image.resolve().parent / ".cache",
            model=args.model, route=args.route, max_dimension=args.max_dimension,
            no_cache=args.no_cache,
        )
        txt = json.dumps({**data, "cache_hit": cache_hit}, indent=2, ensure_ascii=False)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(txt + "\n", encoding="utf-8")
        print(txt)
        return 0
    if args.ui_command == "feedback":
        if not 100 <= args.max_chars <= 100000:
            raise ValueError("--max-chars must be 100..100000")
        prompt = feedback_prompt(args.file, args.audit, args.max_chars)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(prompt + "\n", encoding="utf-8")
        print(prompt)
        return 0
    if args.ui_command == "lavish":
        file = args.file.resolve()
        if not file.is_file() or file.suffix.lower() not in {".html", ".htm"}:
            raise ValueError("Lavish requires a trusted local .html file")
        if not shutil.which("npx"):
            raise RuntimeError("npx not installed. Install Node.js 22+ to use Lavish")
        # Third-party npx will download/run JavaScript code. Must only run by explicit user command.
        print("Opening Lavish AXI. This explicitly runs third-party code through npx.")
        try:
            return subprocess.call(["npx", "-y", "lavish-axi", str(file)], cwd=file.parent)
        except OSError as e:
            raise RuntimeError(str(e)) from e
    return 2


def save_output(report: dict, path: Path | None) -> None:
    """Print sanitized JSON and optionally write to local private file."""
    from .core import write_private_json
    if path:
        write_private_json(path.resolve(), report)
        print(f"Report: {path.resolve()}")
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))


def db_cmd(args) -> int:
    from .db import db_action
    sql = args.sql_file.read_text(encoding="utf-8") if args.db_command == "explain" else None
    report = db_action(args.db_command, url=None, timeout_ms=args.timeout_ms,
                       limit=getattr(args, "limit", 250), tables=getattr(args, "tables", None),
                       sql=sql, analyze=getattr(args, "analyze", False),
                       allow_insecure_db=args.allow_insecure_db)
    save_output(report, args.output)
    return 0


def backend_cmd(args) -> int:
    from .backend import backend_check
    report = backend_check(args.repo, only=args.only, timeout=args.timeout,
                           output_limit=args.output_limit)
    save_output(report, args.output)
    if all(x["status"] == "skipped" for x in report["runs"].values()):
        print("No applicable backend tools were run; install PHPUnit/PHPStan in the project.", file=sys.stderr)
        return 2
    return 1 if any(x["status"] in {"failed", "timeout", "error"}
                    for x in report["runs"].values()) else 0


def api_cmd(args) -> int:
    from .api import check_api, read_contract
    report = check_api(args.url, read_contract(args.contract), allow_remote=args.allow_remote,
                       allow_mutation=args.allow_mutation, bearer_env=args.bearer_env,
                       timeout=args.timeout)
    save_output(report, args.output)
    return 0 if report["passed"] == report["total"] else 1


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.command == "doctor":
        print(f"LeanReview {__version__} | Python {platform.python_version()} | {platform.system()}")
        for cmd in ("git", "codex", "npx", "php"):
            print(f"{cmd}: {shutil.which(cmd) or 'not installed (some features optional)'}")
        for mod in ("psycopg", "pymysql"):
            try:
                __import__(mod)
                print(f"{mod}: available")
            except ImportError:
                print(f"{mod}: not installed (optional DB driver)")
        try:
            import playwright
            print("playwright: installed (browser binaries separately required)")
        except ImportError:
            print("playwright: not installed (pip install -e '.[ui]')")
        return 0 if shutil.which("git") else 2
    try:
        if args.command == "review":
            return review(args)
        if args.command == "ui":
            return ui_cmd(args)
        if args.command == "db":
            return db_cmd(args)
        if args.command == "backend":
            return backend_cmd(args)
        if args.command == "api":
            return api_cmd(args)
        return 2
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as e:
        print(f"leanreview: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
