"""Opt-in read-only database inspection and query-plan review.

The client is deliberately restrictive: metadata queries only, an explicitly
supplied single SELECT for EXPLAIN, no raw row export, and no AI requests.
IMPORTANT: database roles must be SELECT-only; SQL execution can call functions
with side effects and transaction read-only mode is NOT a security boundary.
"""
from __future__ import annotations

import hashlib
import os
import re
import ssl
from contextlib import contextmanager
from urllib.parse import unquote, urlsplit, parse_qs


def connection_config(url: str, *, allow_plaintext_remote: bool = False) -> dict:
    if not url:
        raise ValueError("Set LEANREVIEW_DATABASE_URL in your shell (not in source control)")
    p = urlsplit(url)
    dialects = {"postgres": "postgres", "postgresql": "postgres", "mysql": "mysql"}
    if p.scheme not in dialects or not p.hostname or not p.path.strip("/"):
        raise ValueError("Database URL must be postgresql:// or mysql:// with host/database")
    try:
        port = p.port or (5432 if dialects[p.scheme] == "postgres" else 3306)
    except ValueError as exc:
        raise ValueError("Invalid database port") from exc
    if p.fragment or not p.username:
        raise ValueError("Database URL needs a username and must not contain a fragment")
    q = parse_qs(p.query)
    if set(q) - {"sslmode"}:
        raise ValueError("Only sslmode= is accepted as a database URL query parameter")
    host = p.hostname
    local = host.lower() in {"localhost", "127.0.0.1", "::1"}
    sslmode = q.get("sslmode", ["disable" if local else "verify-full"])[0]
    if sslmode not in {"disable", "require", "verify-full"}:
        raise ValueError("sslmode must be disable, require or verify-full")
    if not local and sslmode != "verify-full" and not allow_plaintext_remote:
        raise ValueError("Remote DB must use sslmode=verify-full (or explicit --allow-insecure-db)")
    return {"dialect": dialects[p.scheme], "host": host, "port": port,
            "database": unquote(p.path.lstrip("/")), "username": unquote(p.username),
            "password": unquote(p.password or ""), "sslmode": sslmode}


@contextmanager
def open_readonly(config: dict, timeout_ms: int = 5000):
    """Open a transaction in read-only mode; ALWAYS roll back on exit."""
    if not 100 <= timeout_ms <= 60000:
        raise ValueError("--timeout-ms must be 100..60000")
    kind = config["dialect"]
    conn = None
    try:
        if kind == "postgres":
            try:
                import psycopg
            except ImportError as e:
                raise RuntimeError("Install PostgreSQL driver: pip install -e '.[postgres]'") from e
            conn = psycopg.connect(host=config["host"], port=config["port"],
                                   dbname=config["database"], user=config["username"],
                                   password=config["password"], sslmode=config["sslmode"],
                                   connect_timeout=5, autocommit=True)
            with conn.cursor() as cursor:
                cursor.execute("BEGIN READ ONLY")
                cursor.execute("SELECT set_config('statement_timeout', %s, true)", (str(timeout_ms),))
                cursor.execute("SELECT set_config('lock_timeout', %s, true)", (str(min(timeout_ms, 2000)),))
        else:
            try:
                import pymysql
            except ImportError as e:
                raise RuntimeError("Install MySQL driver: pip install -e '.[mysql]'") from e
            tls = (ssl.create_default_context() if config["sslmode"] == "verify-full" else
                   ({"check_hostname": False} if config["sslmode"] == "require" else None))
            conn = pymysql.connect(host=config["host"], port=config["port"],
                                   database=config["database"], user=config["username"],
                                   password=config["password"], ssl=tls,
                                   connect_timeout=5, read_timeout=max(2, (timeout_ms // 1000) + 2),
                                   write_timeout=5, autocommit=True)
            with conn.cursor() as cursor:
                cursor.execute("SET SESSION MAX_EXECUTION_TIME = %s", (timeout_ms,))
                cursor.execute("START TRANSACTION READ ONLY")
        yield conn
    except (RuntimeError, ValueError):
        raise
    except Exception:
        # Driver errors may contain connection details or even SQL literals.
        raise RuntimeError("DB connection/query failed; check privileges, TLS, SQL and driver") from None
    finally:
        if conn is not None:
            try:
                _rollback_mysql(conn)  # Explicit ROLLBACK for PG autocommit and MySQL
            finally:
                conn.close()


def _rollback_mysql(conn):
    # Both clients use autocommit=True; explicit transaction needs SQL ROLLBACK.
    with conn.cursor() as c:
        c.execute("ROLLBACK")


def fetch(conn, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor() as c:
        c.execute(sql, params)
        names = [col[0] for col in c.description]
        return [dict(zip(names, row)) for row in c.fetchall()]


PG_TABLES = """SELECT table_schema AS schema_name, table_name FROM information_schema.tables
  WHERE table_type='BASE TABLE' AND table_schema NOT IN ('pg_catalog','information_schema')
  ORDER BY table_schema,table_name LIMIT %s"""
PG_COLUMNS = """SELECT table_schema AS schema_name, table_name, column_name, data_type,
  is_nullable FROM information_schema.columns WHERE table_schema NOT IN ('pg_catalog','information_schema')
  ORDER BY table_schema,table_name,ordinal_position LIMIT %s"""
PG_FK = """SELECT ns.nspname AS schema_name, cl.relname AS table_name, con.conname AS constraint_name,
  pg_get_constraintdef(con.oid, true) AS definition
  FROM pg_constraint con JOIN pg_class cl ON cl.oid=con.conrelid JOIN pg_namespace ns ON ns.oid=cl.relnamespace
  WHERE con.contype='f' AND ns.nspname NOT IN ('pg_catalog','information_schema')
  ORDER BY ns.nspname,cl.relname,con.conname LIMIT %s"""
PG_INDEX = """SELECT schemaname AS schema_name, tablename AS table_name,indexname AS index_name,
  indexdef AS definition FROM pg_indexes WHERE schemaname NOT IN ('pg_catalog','information_schema')
  ORDER BY schemaname,tablename,indexname LIMIT %s"""
MY_TABLES = """SELECT table_schema AS schema_name,table_name FROM information_schema.tables
  WHERE table_schema=DATABASE() AND table_type='BASE TABLE' ORDER BY table_name LIMIT %s"""
MY_COLUMNS = """SELECT table_schema AS schema_name,table_name,column_name,data_type,is_nullable
  FROM information_schema.columns WHERE table_schema=DATABASE()
  ORDER BY table_name,ordinal_position LIMIT %s"""
MY_FK = """SELECT k.table_schema AS schema_name,k.table_name,k.constraint_name,k.column_name,
  k.referenced_table_name,k.referenced_column_name FROM information_schema.key_column_usage k
  WHERE k.table_schema=DATABASE() AND k.referenced_table_name IS NOT NULL
  ORDER BY k.table_name,k.constraint_name,k.ordinal_position LIMIT %s"""
MY_INDEX = """SELECT table_schema AS schema_name,table_name,index_name,column_name,non_unique,seq_in_index
  FROM information_schema.statistics WHERE table_schema=DATABASE()
  ORDER BY table_name,index_name,seq_in_index LIMIT %s"""


def inspect_schema(conn, kind: str, max_items: int = 250, tables: list[str] | None = None) -> dict:
    """Read metadata only. Bound each category; do not fetch table records."""
    if not 1 <= max_items <= 3000:
        raise ValueError("--limit must be 1..3000")
    queries = ([PG_TABLES, PG_COLUMNS, PG_FK, PG_INDEX] if kind == "postgres" else
               [MY_TABLES, MY_COLUMNS, MY_FK, MY_INDEX])
    names = ["tables", "columns", "foreign_keys", "indexes"]
    allowed = set(tables or [])
    # Fetch more metadata than the filtered display limit so requested tables are
    # likely to be found; still bounded. Schema-specific large DBs may need a higher limit.
    results = {}
    for name, q in zip(names, queries):
        if allowed:
            # Apply filter in SQL BEFORE LIMIT so large schemas work correctly.
            # Query is from static constants; names are always bound parameters.
            base = q.rsplit("ORDER BY", 1)[0]
            just_names = sorted({x.rsplit(".", 1)[-1] for x in allowed})
            holes = ",".join(["%s"] * len(just_names))
            filtered = f"SELECT * FROM ({base}) AS meta WHERE table_name IN ({holes}) " \
                       "ORDER BY schema_name,table_name LIMIT %s"
            items = fetch(conn, filtered, (*just_names, max_items))
            items = [r for r in items if r["table_name"] in allowed or
                     f"{r['schema_name']}.{r['table_name']}" in allowed]
        else:
            items = fetch(conn, q, (max_items,))
        results[name] = items
    return {"engine": kind, "tables": results["tables"], "columns": results["columns"],
            "foreign_keys": results["foreign_keys"], "indexes": results["indexes"],
            "limit_per_category": max_items, "metadata_only": True,
            "note": "Results may be incomplete if a category reached its limit; no table rows were read."}


def validate_select(statement: str) -> str:
    """Conservative gate, NOT a SQL sandbox. Rejects CTEs, comments and SQL scripting.

    True protection requires SELECT-only database credentials. No SQL textual
    validator can guarantee a SELECT has no side-effecting functions.
    """
    s = statement.strip()
    if not s or len(s) > 50000:
        raise ValueError("SQL must be a SELECT no larger than 50 KB")
    # Remove SQL quoted strings / identifiers before token checking. Reject dollar
    # quotes, comments, backticks and CTEs rather than try to be a full SQL parser.
    scrub = re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", "'LITERAL'", s)
    if any(x in scrub for x in (";", "--", "/*", "*/", "#", "$$", "`")):
        raise ValueError("Comments, multiple statements or complex quoting are not accepted")
    if not re.match(r"^SELECT\s+", scrub, flags=re.I):
        raise ValueError("Only a single SELECT statement is supported (no CTE / DML / DDL)")
    if re.search(r"\b(?:INTO|FOR\s+(?:UPDATE|SHARE)|LOCK\s+IN\s+SHARE|OUTFILE|DUMPFILE|"
                 r"LOAD_FILE|SLEEP|PG_SLEEP|DBLINK|LO_IMPORT|PG_ADVISORY|"
                 r"GET_LOCK|RELEASE_LOCK|BENCHMARK|SET_CONFIG|NEXTVAL|SETVAL|"
                 r"PG_ADVISORY_[A-Z_]+|PG_READ_FILE|PG_LS_DIR)\b", scrub, flags=re.I):
        raise ValueError("Query contains a potentially unsafe construct")
    return s


def _scan_pg_node(node, out: list):
    if not isinstance(node, dict) or len(out) >= 80:
        return
    entry = {k: node[k] for k in ("Node Type", "Relation Name", "Index Name", "Plan Rows",
                                   "Actual Rows", "Actual Total Time", "Total Cost") if k in node}
    out.append(entry)
    for sub in node.get("Plans", []):
        _scan_pg_node(sub, out)


def summarize_plan(kind: str, result: object, analyze: bool) -> dict:
    """Extract plan metrics without SQL constants/filters/row data."""
    import json
    if kind == "postgres":
        raw = result
        if isinstance(raw, str):
            raw = json.loads(raw)
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        nodes: list[dict] = []
        _scan_pg_node(raw.get("Plan", {}), nodes)
        findings = []
        for n in nodes:
            if n.get("Node Type") == "Seq Scan":
                findings.append({"rule": "sequential-scan", "table": n.get("Relation Name"),
                                 "message": "Sequential scan: verify table size and selectivity (may be appropriate)"})
            estimate, actual = n.get("Plan Rows"), n.get("Actual Rows")
            if analyze and isinstance(estimate, (float, int)) and isinstance(actual, (float, int)) and estimate > 0 and actual > 0:
                factor = round(max(estimate / actual, actual / estimate), 1)
                if factor >= 10:
                    findings.append({"rule": "row-estimate-mismatch", "node": n.get("Node Type"), "factor": factor})
        return {"engine": kind, "analyze": analyze, "nodes": nodes, "findings": findings}
    # MySQL 8.0's EXPLAIN ANALYZE returns a TREE string. Non-analyze
    # FORMAT=JSON gives structured access/rows/key data; do not echo filters.
    if analyze:
        sanitized = re.sub(r"'(?:''|[^'])*'", "'[LITERAL]'", str(result))
        return {"engine": kind, "analyze": True, "plan_summary": sanitized[:10000],
                "note": "MySQL TREE plan may include SQL identifiers; keep report private."}
    raw = json.loads(result) if isinstance(result, str) else result
    nodes = []
    def walk(o):
        if len(nodes) >= 80:
            return
        if isinstance(o, dict):
            if "table_name" in o:
                nodes.append({k: o[k] for k in ("table_name", "access_type", "key", "rows_examined_per_scan") if k in o})
            for v in o.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(raw)
    return {"engine": kind, "analyze": False, "nodes": nodes,
            "findings": [{"rule": "full-scan", "table": n.get("table_name"),
                          "message": "Table scan: assess row count and filtering"}
                         for n in nodes if n.get("access_type") == "ALL"]}


def explain(conn, kind: str, statement: str, analyze: bool = False) -> dict:
    s = validate_select(statement)
    if kind == "postgres":
        query = f"EXPLAIN (FORMAT JSON, ANALYZE {'TRUE' if analyze else 'FALSE'}) " + s
    else:
        query = ("EXPLAIN ANALYZE " if analyze else "EXPLAIN FORMAT=JSON ") + s
    with conn.cursor() as c:
        c.execute(query)
        rows = c.fetchall()
    if kind == "mysql":
        plan = rows[0][0]
    else:
        plan = rows[0][0]
    result = summarize_plan(kind, plan, analyze)
    result["sql_sha256"] = hashlib.sha256(s.encode()).hexdigest()
    result["sql_included"] = False
    return result


def db_action(action: str, *, url: str | None, timeout_ms: int = 5000,
              limit: int = 250, tables: list[str] | None = None, sql: str | None = None,
              analyze: bool = False, allow_insecure_db: bool = False) -> dict:
    config = connection_config(url or os.getenv("LEANREVIEW_DATABASE_URL", ""),
                               allow_plaintext_remote=allow_insecure_db)
    if action == "explain":
        if sql is None:
            raise ValueError("Use --sql-file to supply a SELECT")
        validate_select(sql)  # reject *before* making network connection
    with open_readonly(config, timeout_ms) as conn:
        if action == "inspect":
            return inspect_schema(conn, config["dialect"], limit, tables)
        return explain(conn, config["dialect"], sql or "", analyze)
