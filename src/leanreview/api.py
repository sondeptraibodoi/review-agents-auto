"""Opt-in lightweight API smoke/contract tests using Python standard library.

No response bodies or authentication tokens are stored. Redirects are blocked,
local targets are the default, mutating HTTP methods need explicit permission.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .ui import validate_url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_spec(spec: object, allow_mutation: bool) -> list[dict]:
    if not isinstance(spec, dict) or not isinstance(spec.get("endpoints"), list):
        raise ValueError("API contract must contain an 'endpoints' array")
    cases = spec["endpoints"]
    if not 1 <= len(cases) <= 100:
        raise ValueError("Contract must contain 1..100 endpoints")
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Each endpoint must be an object")
        path = case.get("path")
        if (not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or
                "\\" in path or "#" in path or "\x00" in path):
            raise ValueError("Endpoint path must begin with / and must not escape host")
        if not isinstance(case.get("method", "GET"), str):
            raise ValueError("method must be a string")
        method = case.get("method", "GET").upper()
        if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("Unsupported HTTP method")
        if method not in {"GET", "HEAD"} and not allow_mutation:
            raise ValueError("POST/PUT/PATCH/DELETE require --allow-mutation")
        if not isinstance(case.get("expected_status", 200), int) or not 100 <= case.get("expected_status", 200) <= 599:
            raise ValueError("expected_status must be an HTTP code")
        keys = case.get("required_keys", [])
        if not isinstance(keys, list) or len(keys) > 30 or any(not isinstance(k, str) or not k for k in keys):
            raise ValueError("required_keys must be a list of key paths (<=30)")
        if "max_ms" in case and (not isinstance(case["max_ms"], int) or not 1 <= case["max_ms"] <= 120000):
            raise ValueError("max_ms must be 1..120000 milliseconds")
        if "json" in case and method in {"GET", "HEAD"}:
            raise ValueError("JSON request body requires POST/PUT/PATCH/DELETE")
        if "json" in case and not isinstance(case["json"], (list, dict)):
            raise ValueError("json request body must be an object or array")
    return cases


def _key_exists(data, key: str) -> bool:
    for part in key.split("."):
        if isinstance(data, dict) and part in data:
            data = data[part]
        elif isinstance(data, list) and part.isdecimal() and int(part) < len(data):
            data = data[int(part)]
        else:
            return False
    return True


def check_api(base_url: str, spec: dict, *, allow_remote: bool = False,
              allow_mutation: bool = False, bearer_env: str | None = None,
              timeout: float = 5.0, max_bytes: int = 262144) -> dict:
    validate_url(base_url, allow_remote)
    cases = validate_spec(spec, allow_mutation)
    if not 0.5 <= timeout <= 60 or not 1024 <= max_bytes <= 1048576:
        raise ValueError("timeout must be 0.5..60 and max_bytes 1024..1048576")
    base = base_url.rstrip("/") + "/"
    opener = urllib.request.build_opener(NoRedirect())
    token = os.environ.get(bearer_env, "") if bearer_env else ""
    if bearer_env and not token:
        raise ValueError("Bearer token environment variable is missing or empty")
    results = []
    for c in cases:
        path = c["path"]
        url = urljoin(base, path.lstrip("/"))
        # The only possible target is the caller-supplied origin.
        if urlsplit(url).netloc != urlsplit(base).netloc:
            raise ValueError("Endpoint cannot change origin")
        method = c.get("method", "GET").upper()
        body = json.dumps(c["json"]).encode() if "json" in c else None
        headers = {"Accept": "application/json", "User-Agent": "LeanReview/0.3"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        started = time.monotonic()
        status, ctype, content, error = None, "", b"", None
        try:
            with opener.open(request, timeout=timeout) as response:
                status, ctype = response.status, response.headers.get("Content-Type", "")
                content = response.read(max_bytes + 1)
        except urllib.error.HTTPError as exc:
            status, ctype = exc.code, exc.headers.get("Content-Type", "")
            content = exc.read(max_bytes + 1)
            if 300 <= status <= 399:
                error = "redirect-blocked"
        except (urllib.error.URLError, OSError, TimeoutError, ValueError):
            error = "request-failed"
        duration = round((time.monotonic() - started) * 1000)
        issues = []
        if error:
            issues.append(error)
        if status != c.get("expected_status", 200):
            issues.append("unexpected-status")
        if len(content) > max_bytes:
            issues.append("response-too-large")
        keys = c.get("required_keys", [])
        if keys and len(content) <= max_bytes:
            try:
                payload = json.loads(content)
            except (ValueError, UnicodeError):
                issues.append("invalid-json")
            else:
                if not all(_key_exists(payload, key) for key in keys):
                    issues.append("missing-required-keys")
        if "max_ms" in c and duration > c["max_ms"]:
            issues.append("slow-response")
        results.append({"path": path, "method": method, "status": status,
                        "expected_status": c.get("expected_status", 200),
                        "duration_ms": duration, "content_type": ctype.split(";")[0][:80],
                        "passed": not issues, "issues": issues})
    return {"base_origin": urlsplit(base).scheme + "://" + urlsplit(base).netloc,
            "passed": sum(bool(r["passed"]) for r in results), "total": len(results),
            "results": results, "token_cost": 0,
            "note": "No response bodies or Authorization headers saved. API calls may have side effects."}


def read_contract(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Contract must be a JSON object")
    return data
