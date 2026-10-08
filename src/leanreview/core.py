"""Offline-first patch selection, redaction and lightweight deterministic checks."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

EXCLUDE_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "composer.lock", "Cargo.lock",
    "poetry.lock", "uv.lock", "Gemfile.lock", "bun.lockb",
}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".ico",
              ".woff", ".woff2", ".ttf", ".mp4", ".mp3", ".exe", ".dll", ".so", ".map"}
SKIP_DIRS = {"dist", "build", "coverage", ".next", ".angular", "vendor", "node_modules", ".venv"}
SECRET = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?token|client[_-]?secret|password|private[_-]?key|"
    r"authorization|secret[_-]?key)\s*[:=]\s*[\"']?)([^\s\"',;]+)"
)
AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
GITHUB_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
PATCH_FILE = re.compile(r"^\+\+\+ b/(.*)$", re.M)
HEADER = re.compile(r"^diff --git a/(.*?) b/(.*?)$", re.M)
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
REVIEW_VERSION = "v2:diff-hunks:redaction:v2:prompt:v2"


@dataclass
class Slice:
    patch: str
    files: list[str]
    ignored: list[str]
    estimate: int
    truncated: bool
    sha256: str


def run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=45)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git failed")
    return result.stdout


def get_diff(cwd: Path, base: str | None = None, staged: bool = False) -> str:
    if base and staged:
        raise ValueError("Choose either --base or --staged")
    common = ["diff", "--no-ext-diff", "--no-color", "--no-textconv", "--unified=3"]
    if base:
        run_git(["rev-parse", "--verify", f"{base}^{{commit}}"], cwd)
        return run_git([*common, f"{base}...HEAD", "--"], cwd)
    return run_git([*common, *(["--cached"] if staged else []), "--"], cwd)


def sensitive_path(path: str) -> bool:
    p = PurePosixPath(path.replace("\\", "/"))
    name = p.name.lower()
    return (name == ".env" or name.startswith(".env.") or
            name in {"id_rsa", "id_ed25519", "credentials.json", "service-account.json"} or
            p.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".keystore"})


def skip_path(path: str) -> bool:
    p = PurePosixPath(path.replace("\\", "/"))
    return (p.name in EXCLUDE_NAMES or p.suffix.lower() in BINARY_EXT or
            p.name.endswith(".min.js") or any(v in SKIP_DIRS for v in p.parts) or
            sensitive_path(path))


def redact(value: str) -> str:
    value = SECRET.sub(lambda m: m.group(1) + "[REDACTED]", value)
    value = AWS_KEY.sub("[REDACTED_AWS_KEY]", value)
    value = GITHUB_TOKEN.sub("[REDACTED_GITHUB_TOKEN]", value)
    return OPENAI_KEY.sub("[REDACTED_OPENAI_KEY]", value)


def _score_file(path: str) -> int:
    """Rank likely bug-bearing paths above tests/docs. Deterministic & cheap."""
    path = path.lower()
    points = 0
    if any(p in path for p in ("auth", "permission", "migration", "sql", "payment", "security")):
        points += 20
    if path.endswith((".php", ".sql", ".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs")):
        points += 10
    if any(p in path for p in ("test", "spec", "docs", "readme", ".md")):
        points -= 12
    return points


def _file_path(block: str) -> str:
    match = PATCH_FILE.search(block) or HEADER.search(block)
    return match.group(1) if match else "unknown"


def slice_diff(diff: str, max_chars: int = 28000, max_files: int = 35) -> Slice:
    if max_chars < 200 or max_files < 1:
        raise ValueError("max_chars >= 200 and max_files >= 1 required")
    blocks = [b for b in re.split(r"(?=^diff --git )", diff, flags=re.M) if b.strip()]
    ranked = sorted(enumerate(blocks), key=lambda item: (-_score_file(_file_path(item[1])), item[0]))
    chosen: list[tuple[int, str, str]] = []
    ignored: list[str] = []
    remaining = max_chars
    truncated = False
    for index, block in ranked:
        path = _file_path(block)
        if skip_path(path):
            ignored.append(path)
            continue
        if len(chosen) >= max_files or remaining < 200:
            ignored.append(path)
            truncated = True
            continue
        clean = redact(block)
        if len(clean) > remaining:
            # Preserve complete lines, never leak arbitrary tails. Partial review is flagged.
            end = clean.rfind("\n", 0, remaining - 14)
            if end < 100:
                ignored.append(path)
                truncated = True
                continue
            clean = clean[:end + 1] + "[TRUNCATED]\n"
            truncated = True
        chosen.append((index, path, clean))
        remaining -= len(clean)
    # Present chosen files in their original Git order, not risk-ranking order.
    chosen.sort(key=lambda x: x[0])
    patch = "".join(item[2] for item in chosen)
    return Slice(patch, [item[1] for item in chosen], ignored,
                 (len(patch) + 3) // 4, truncated,
                 hashlib.sha256((REVIEW_VERSION + "\n" + patch).encode()).hexdigest())


def offline_findings(patch: str) -> list[dict]:
    """Advisory heuristics on added lines only, not a vulnerability scanner."""
    rules = [
        ("debug-output", r"\b(?:dd|dump|var_dump|console\.log|print_r)\s*\(", "Debug output added"),
        ("sql-raw", r"\b(?:DB::raw|whereRaw|selectRaw)\s*\(", "Raw SQL: verify parameter binding"),
        ("dangerous-eval", r"\b(?:eval|exec)\s*\(", "Dynamic evaluation: verify trust boundary"),
        ("secret-like", r"\[REDACTED(?:_[A-Z_]+)?\]", "Possible credential added; verify before commit"),
        ("php-die", r"\b(?:die|exit)\s*\(", "Process termination added"),
    ]
    findings: list[dict] = []
    file, line = "unknown", 0
    for s in patch.splitlines():
        m = PATCH_FILE.match(s)
        if m:
            file, line = m.group(1), 0
            continue
        h = HUNK.match(s)
        if h:
            line = int(h.group(1))
        elif s.startswith("+") and not s.startswith("+++"):
            for rid, pattern, message in rules:
                if re.search(pattern, s[1:], re.I):
                    findings.append({"rule": rid, "file": file, "line": line,
                                     "severity": "warning", "message": message})
            line += 1
        elif s.startswith(" "):
            line += 1
    return findings


def cache_file(root: Path, digest: str, agent: str, model: str) -> Path:
    key = hashlib.sha256(f"{REVIEW_VERSION}:{digest}:{agent}:{model}".encode()).hexdigest()
    return root / ".leanreview" / "cache" / (key + ".json")


def write_private_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    import os
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def output_report(s: Slice, findings: list[dict], ai: dict | None = None, cached: bool = False) -> dict:
    return {
        "files_reviewed": s.files, "files_ignored": s.ignored, "estimated_patch_tokens": s.estimate,
        "truncated": s.truncated, "sha256": s.sha256, "offline_findings": findings,
        "ai": ai, "ai_cache_hit": cached,
        "note": "Token count approximates patch size only (characters / 4), not billed model tokens.",
    }
