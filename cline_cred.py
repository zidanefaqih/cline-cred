#!/usr/bin/env python3
"""
cline_cred.py - inject / list / verify / delete Cline (WorkOS OAuth) credentials
in a 9router-family SQLite DB (VansRouter, 9router, and any fork of it).

WHY THIS EXISTS
---------------
Cline in VansRouter/9router is OAuth-only: `authModes` is not declared for the
`cline` provider, so `POST /api/providers` (and `/api/providers/bulk`) reject it
with 400 "Invalid provider". The only official path is the browser authorize
flow that pastes an authorization *code*. There is no bulk-add and no
token-import endpoint for Cline (unlike codex/cursor/kiro which each ship
`/api/oauth/<p>/import*`).

The API-only workaround - `POST /api/oauth/cline/exchange` with a raw `eyJ...`
JWT - stores `authType:"access_token"` with NO refreshToken, so the connection
dies when the 1h WorkOS access token expires.

This script writes the same row the dashboard OAuth flow would have written,
including the refresh token, directly into `providerConnections`.

DB SCHEMA (verified live on VansRouter 0.91.33, schemaVersion 8)
----------------------------------------------------------------
  providerConnections(
    id TEXT PRIMARY KEY, provider TEXT NOT NULL, authType TEXT NOT NULL,
    name TEXT, email TEXT, priority INTEGER, isActive INTEGER DEFAULT 1,
    data TEXT NOT NULL,          -- JSON blob, everything that is not a column
    createdAt TEXT NOT NULL, updatedAt TEXT NOT NULL
  )

  data JSON for a Cline OAuth row:
    accessToken   "workos:<jwt>"      <- getClineAccessToken() adds the prefix
    refreshToken  "<workos rt>"
    expiresAt     "2026-10-02T13:19:11.563Z"
    expiresIn     3599
    lastRefreshAt "<iso>"
    testStatus    "active"
    backoffLevel  0
    providerSpecificData {firstName, lastName}
    (errorCode/lastError/lastErrorAt/rateLimitedUntil = null)
    (modelLock_<modelId> = null - per-model health locks, reset on activation)

NO RESTART NEEDED: the app reads providerConnections from SQLite per request
(no in-memory connection cache), so rows appear immediately in the dashboard,
`/api/providers` and routing.

SAFETY
------
- Backs the DB up to <data>/db/backups/ before any write (unless --no-backup).
- Dedupes on (provider, authType=oauth, email) like createProviderConnection
  does, so re-running updates the existing row instead of duplicating it.
- Never prints token values.

USAGE
-----
  # what is already in the DB (no secrets printed)
  python3 cline_cred.py list

  # preview an import from the CPA auth files, write nothing
  python3 cline_cred.py import --cpa --only anispena12 --dry-run

  # import every Cline account CPA has that this DB does not
  python3 cline_cred.py import --cpa --missing-only

  # import one file (CPA shape, VansRouter shape, or a list of them)
  python3 cline_cred.py import --file /tmp/cline.json

  # have the app itself prove the injected token works (upstream users/me probe,
  # and it will refresh via the injected refresh token when stale)
  python3 cline_cred.py verify --email someone@example.com

  # remove a connection
  python3 cline_cred.py delete --email someone@example.com

Targets (--instance resolves the host data dir from the running container):
  --instance vansrouter   -> default, port 20130
  --instance 9router      -> port 20128
  --db /path/data.sqlite  -> explicit, overrides --instance
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

DEFAULT_INSTANCES = {
    # "~" so the default path is correct on anyone's machine, not just the developer's.
    "vansrouter": {"container": "vansrouter", "data": "~/.vansrouter", "port": 20130},
    "9router": {"container": "9router", "data": "~/.9router", "port": 20128},
}
CPA_CONTAINER = "cli-proxy-api"
CPA_AUTHS_IN_CONTAINER = "/root/.cli-proxy-api"
CPA_AUTHS_HOST = "~/cliproxyapi/auths"

JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
MODEL_LOCK_PREFIX = "modelLock_"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def to_iso_z(value) -> str | None:
    """Normalize CPA/VansRouter expiry shapes to ISO-8601 with Z."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(int(text), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_access_token(token: str) -> str:
    """Mirror open-sse/shared/clineAuth.js getClineAccessToken()."""
    token = (token or "").strip()
    if not token:
        return ""
    if token.lower().startswith("workos:"):
        return token
    return f"workos:{token}" if JWT_RE.match(token) else token


def jwt_email(token: str) -> str:
    """Best-effort email from a JWT payload (never printed unless asked)."""
    raw = (token or "").split("workos:")[-1]
    parts = raw.split(".")
    if len(parts) < 2:
        return ""
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return ""
    return str(claims.get("email") or claims.get("preferred_username") or "")


def die(msg: str, code: int = 1):
    print(f"[!] {msg}", file=sys.stderr)
    raise SystemExit(code)


# --------------------------------------------------------------------------- #
# target resolution
# --------------------------------------------------------------------------- #
def resolve_db_path(args) -> str:
    if args.db:
        return os.path.expanduser(args.db)
    inst = DEFAULT_INSTANCES.get(args.instance)
    if not inst:
        die(f"unknown --instance {args.instance!r}; known: {', '.join(DEFAULT_INSTANCES)}")
    data_dir = os.path.expanduser(inst["data"])
    # Prefer the live container's mount so we never guess the data dir.
    try:
        out = subprocess.run(
            ["docker", "inspect", inst["container"], "--format",
             '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}'],
            capture_output=True, text=True, timeout=15,
        )
        mounted = out.stdout.strip()
        if mounted:
            data_dir = os.path.expanduser(mounted)
    except Exception:
        pass
    return os.path.join(data_dir, "db", "data.sqlite")


def resolve_port(args) -> int:
    if args.port:
        return args.port
    inst = DEFAULT_INSTANCES.get(args.instance)
    return inst["port"] if inst else 20130


def connect(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        die(f"DB not found: {db_path}")
    conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def backup_db(conn: sqlite3.Connection, db_path: str) -> str:
    backup_dir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(backup_dir, f"cline-cred-inject-{ts}.sqlite")
    # Two writes inside the same second would otherwise collide and the second would
    # silently overwrite the first, losing the snapshot taken before it. Bump to
    # -1, -2, ... until the name is free. Happens when deleting several accounts in
    # one batch, which is exactly when the backups matter most.
    if os.path.exists(dest):
        stem, ext = dest[: -len(".sqlite")], ".sqlite"
        n = 1
        while os.path.exists(f"{stem}-{n}{ext}"):
            n += 1
        dest = f"{stem}-{n}{ext}"
    with sqlite3.connect(dest) as target:
        conn.backup(target)
    return dest


def also_copy_sidecar(db_path: str) -> list[str]:
    """WAL/SHM are not part of the .backup() copy; keep them next to it too."""
    made = []
    for suffix in ("-wal", "-shm"):
        src = db_path + suffix
        if os.path.exists(src):
            dest = src + f".bak-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
            if os.path.exists(dest):
                n = 1
                while os.path.exists(f"{dest}-{n}"):
                    n += 1
                dest = f"{dest}-{n}"
            try:
                shutil.copy2(src, dest)
                made.append(dest)
            except OSError:
                pass
    return made


# --------------------------------------------------------------------------- #
# credential sources
# --------------------------------------------------------------------------- #
def normalize_credential(raw: dict, source: str) -> dict | None:
    """Accept CPA / VansRouter / SuperApp shapes -> canonical credential dict."""
    if not isinstance(raw, dict):
        return None
    if str(raw.get("type", "cline")).lower() not in ("cline", ""):
        return None

    access = (
        raw.get("accessToken") or raw.get("access_token")
        or (raw.get("credentials") or {}).get("accessToken")
        or (raw.get("credentials") or {}).get("access_token") or ""
    )
    refresh = (
        raw.get("refreshToken") or raw.get("refresh_token")
        or (raw.get("credentials") or {}).get("refreshToken")
        or (raw.get("credentials") or {}).get("refresh_token") or ""
    )
    access = normalize_access_token(str(access))
    refresh = str(refresh).strip()
    if not access and not refresh:
        return None

    email = str(
        raw.get("email") or (raw.get("credentials") or {}).get("email") or jwt_email(access)
    ).strip()

    expires = (
        raw.get("expiresAt") or raw.get("expires_at") or raw.get("expired")
        or (raw.get("credentials") or {}).get("expiresAt")
        or (raw.get("credentials") or {}).get("expires_at")
        or (raw.get("credentials") or {}).get("expired")
    )
    specific = raw.get("providerSpecificData") or (raw.get("credentials") or {}).get("providerSpecificData") or {}

    return {
        "accessToken": access,
        "refreshToken": refresh,
        "email": email,
        "expiresAt": to_iso_z(expires),
        "firstName": str(specific.get("firstName") or raw.get("firstName") or ""),
        "lastName": str(specific.get("lastName") or raw.get("lastName") or ""),
        "disabled": bool(raw.get("disabled", False)),
        "source": source,
    }


def load_from_file(path: str) -> list[dict]:
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        die(f"file not found: {path}")
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        die(f"cannot parse {path}: {exc}")
    items = data if isinstance(data, list) else [data]
    out = []
    for item in items:
        cred = normalize_credential(item, path)
        if cred:
            out.append(cred)
    return out


def load_from_cpa(only: str | None) -> list[dict]:
    """Read Cline auth files CPA owns. Tries docker exec first (files are
    root-only on the host), then a local dir."""
    files: list[tuple[str, str]] = []
    try:
        listing = subprocess.run(
            ["docker", "exec", CPA_CONTAINER, "sh", "-c",
             f"ls {CPA_AUTHS_IN_CONTAINER}/cline-*.json 2>/dev/null"],
            capture_output=True, text=True, timeout=20,
        )
        if listing.returncode == 0:
            for name in listing.stdout.split():
                files.append(("docker", name))
    except Exception:
        pass

    if not files and os.path.isdir(CPA_AUTHS_HOST):
        for name in sorted(os.listdir(CPA_AUTHS_HOST)):
            if name.startswith("cline-") and name.endswith(".json"):
                files.append(("host", os.path.join(CPA_AUTHS_HOST, name)))

    if not files:
        die(f"no Cline auth files found (docker exec {CPA_CONTAINER}, or {CPA_AUTHS_HOST})")

    out = []
    for kind, ref in files:
        if only and only.lower() not in ref.lower():
            continue
        try:
            if kind == "docker":
                blob = subprocess.run(
                    ["docker", "exec", CPA_CONTAINER, "cat", ref],
                    capture_output=True, text=True, timeout=20,
                ).stdout
            else:
                with open(ref, encoding="utf-8") as fh:
                    blob = fh.read()
            raw = json.loads(blob)
        except Exception as exc:  # noqa: BLE001
            print(f"[!] skip {os.path.basename(ref)}: {exc}", file=sys.stderr)
            continue
        cred = normalize_credential(raw, os.path.basename(ref))
        if cred:
            out.append(cred)
    if not out:
        die("no usable Cline credentials parsed (all files skipped or filtered out)")
    return out


# --------------------------------------------------------------------------- #
# DB ops
# --------------------------------------------------------------------------- #
def list_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM providerConnections WHERE provider='cline' ORDER BY priority"
    ).fetchall()
    out = []
    for row in rows:
        data = json.loads(row["data"] or "{}")
        exp = data.get("expiresAt")
        out.append({
            "id": row["id"],
            "authType": row["authType"],
            "name": row["name"],
            "email": row["email"],
            "priority": row["priority"],
            "isActive": row["isActive"],
            "testStatus": data.get("testStatus"),
            "expiresAt": exp,
            "hasRefreshToken": bool(data.get("refreshToken")),
            "expired": _is_expired(exp),
        })
    return out


def _is_expired(exp: str | None) -> bool | None:
    if not exp:
        return None
    try:
        dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt <= datetime.now(timezone.utc)


def find_row(conn: sqlite3.Connection, email: str):
    if not email:
        return None
    for row in conn.execute(
        "SELECT * FROM providerConnections WHERE provider='cline'"
    ).fetchall():
        same_email = (row["email"] or "").strip().lower() == email.strip().lower()
        if not same_email:
            continue
        if row["authType"] in ("oauth", "access_token"):
            return row
    return None


def build_data(cred: dict, existing: dict | None) -> dict:
    """Mirror what createProviderConnection + resetHealthStateOnActivation store."""
    expires_at = cred["expiresAt"] or (existing or {}).get("expiresAt")
    expires_in = 3600
    if expires_at:
        try:
            dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            expires_in = max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))
        except ValueError:
            pass

    data = dict(existing or {})
    data.update({
        "accessToken": cred["accessToken"] or data.get("accessToken", ""),
        "refreshToken": cred["refreshToken"] or data.get("refreshToken", ""),
        "expiresAt": expires_at,
        "expiresIn": expires_in,
        "lastRefreshAt": now_iso(),
        "testStatus": "active",
        "lastError": None,
        "lastErrorAt": None,
        "errorCode": None,
        "rateLimitedUntil": None,
        "backoffLevel": 0,
        "providerSpecificData": {
            **(data.get("providerSpecificData") or {}),
            "firstName": cred["firstName"],
            "lastName": cred["lastName"],
        },
    })
    # Activation clears per-model health locks (app does the same).
    for key in list(data.keys()):
        if key.startswith(MODEL_LOCK_PREFIX):
            data[key] = None
    return data


def upsert(conn: sqlite3.Connection, cred: dict, priority: int | None) -> tuple[str, str]:
    """Returns (action, connection_id)."""
    email = cred["email"]
    if not email:
        die(f"credential from {cred['source']} has no email and no JWT claim to derive one from")
    row = find_row(conn, email)

    if row:
        existing_data = json.loads(row["data"] or "{}")
        data = build_data(cred, existing_data)
        conn.execute(
            "UPDATE providerConnections SET data=?, email=?, updatedAt=?, isActive=?, name=? WHERE id=?",
            (
                json.dumps(data, separators=(",", ":")),
                email,
                now_iso(),
                0 if cred["disabled"] else 1,
                row["name"] or email,
                row["id"],
            ),
        )
        return ("updated", row["id"])

    max_priority = conn.execute(
        "SELECT COALESCE(MAX(priority), 0) FROM providerConnections WHERE provider='cline'"
    ).fetchone()[0]
    conn_id = str(uuid.uuid4())
    ts = now_iso()
    data = build_data(cred, None)
    conn.execute(
        """INSERT INTO providerConnections
             (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
           VALUES (?, 'cline', 'oauth', ?, ?, ?, ?, ?, ?, ?)""",
        (
            conn_id, email, email,
            priority if priority is not None else max_priority + 1,
            0 if cred["disabled"] else 1,
            json.dumps(data, separators=(",", ":")),
            ts, ts,
        ),
    )
    return ("inserted", conn_id)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_list(args):
    db_path = resolve_db_path(args)
    with connect(db_path) as conn:
        rows = list_rows(conn)
    print(f"DB: {db_path}")
    print(f"{len(rows)} cline connection(s)\n")
    for r in rows:
        flag = "expired" if r["expired"] else ("ok" if r["expired"] is not None else "no-expiry")
        rt = "RT" if r["hasRefreshToken"] else "--"
        print(f"  [{r['priority']:>2}] {r['email'] or r['name']}")
        print(f"       authType={r['authType']} active={r['isActive']} test={r['testStatus']} "
              f"{rt} exp={r['expiresAt'] or '-'} ({flag})")
        print(f"       id={r['id']}")


def cmd_import(args):
    if args.cpa:
        creds = load_from_cpa(args.only)
    elif args.file:
        creds = load_from_file(args.file)
    else:
        die("nothing to import: pass --cpa or --file")
        return

    db_path = resolve_db_path(args)
    if args.dry_run:
        with connect(db_path) as conn:
            present = {r["email"].lower() for r in list_rows(conn) if r["email"]}
        print(f"DRY RUN - target {db_path}\n")
        for c in creds:
            known = c["email"].lower() in present
            print(f"  {c['email']:<40} source={c['source']:<45} "
                  f"{'UPDATE existing' if known else 'INSERT new'} "
                  f"rt={'yes' if c['refreshToken'] else 'NO'}")
        print(f"\n{len(creds)} credential(s) parsed, nothing written.")
        return

    if args.missing_only:
        with connect(db_path) as conn:
            present = {r["email"].lower() for r in list_rows(conn) if r["email"]}
        before = len(creds)
        creds = [c for c in creds if c["email"].lower() not in present]
        print(f"[i] --missing-only: {before - len(creds)} already present, {len(creds)} to add")
        if not creds:
            print("nothing to do.")
            return

    with connect(db_path) as conn:
        if not args.no_backup:
            dest = backup_db(conn, db_path)
            side = also_copy_sidecar(db_path)
            print(f"[i] backup: {dest}" + (f" (+{len(side)} sidecar)" if side else ""))
        results = []
        for c in creds:
            action, conn_id = upsert(conn, c, args.priority)
            results.append((action, conn_id, c["email"]))
        conn.commit()

    print(f"\n{len(results)} connection(s) written to {db_path}\n")
    for action, conn_id, email in results:
        print(f"  {action:<8} {email:<40} id={conn_id}")

    if args.verify:
        print("\nverifying through the app (upstream users/me probe):")
        for _, conn_id, email in results:
            ok, msg = app_verify(resolve_port(args), conn_id)
            print(f"  {'PASS' if ok else 'FAIL'} {email:<40} {msg}")


def cmd_verify(args):
    port = resolve_port(args)
    db_path = resolve_db_path(args)
    with connect(db_path) as conn:
        rows = list_rows(conn)
    if args.email:
        needle = args.email.strip().lower()
        rows = [r for r in rows if needle in (r["email"] or "").lower()]
        if not rows:
            die(f"no cline connection matching '{args.email}' (substring match on email)")
    if not rows:
        die("no cline connection")
    for r in rows:
        ok, msg = app_verify(port, r["id"])
        print(f"  {'PASS' if ok else 'FAIL'} {r['email'] or r['name']:<40} {msg}")


def app_verify(port: int, conn_id: str) -> tuple[bool, str]:
    url = f"http://127.0.0.1:{port}/api/providers/{conn_id}/test"
    req = urllib.request.Request(url, data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code} {exc.read()[:160].decode(errors='replace')}"
    except Exception as exc:  # noqa: BLE001
        return False, f"request failed: {exc}"
    if body.get("valid"):
        return True, f"valid=true refreshed={body.get('refreshed', False)}"
    return False, f"valid=false error={body.get('error')}"


def cmd_delete(args):
    if not args.email and not args.id:
        die("pass --email or --id")
    db_path = resolve_db_path(args)
    with connect(db_path) as conn:
        if args.id:
            targets = conn.execute(
                "SELECT * FROM providerConnections WHERE id=?", (args.id,)
            ).fetchall()
        else:
            targets = [r for r in conn.execute(
                "SELECT * FROM providerConnections WHERE provider='cline'"
            ).fetchall() if (r["email"] or "").lower() == args.email.lower()]
        if not targets:
            die("no matching cline connection")
        if not args.no_backup:
            dest = backup_db(conn, db_path)
            print(f"[i] backup: {dest}")
        for row in targets:
            conn.execute("DELETE FROM providerConnections WHERE id=?", (row["id"],))
            print(f"  deleted {row['email'] or row['name']} id={row['id']}")
        conn.commit()


def db_summary(path: str):
    """Read-only peek: does this file look like a 9router DB, and how many cline rows?"""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "providerConnections" not in tables:
            conn.close()
            return None
        total = conn.execute("SELECT COUNT(*) FROM providerConnections").fetchone()[0]
        cline = conn.execute(
            "SELECT COUNT(*) FROM providerConnections WHERE provider='cline'"
        ).fetchone()[0]
        conn.close()
        return {"total": total, "cline": cline}
    except Exception:
        return None


def cmd_find_db(args):
    """Locate candidate data.sqlite files. Read-only, writes nothing."""
    found: dict[str, str] = {}

    # 1. Every running container that mounts something at /app/data.
    try:
        names = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=20,
        ).stdout.split()
    except Exception:
        names = []
    for name in names:
        try:
            src = subprocess.run(
                ["docker", "inspect", name, "--format",
                 '{{range .Mounts}}{{if eq .Destination "/app/data"}}{{.Source}}{{end}}{{end}}'],
                capture_output=True, text=True, timeout=15,
            ).stdout.strip()
        except Exception:
            continue
        if not src:
            continue
        cand = os.path.join(os.path.expanduser(src), "db", "data.sqlite")
        if os.path.exists(cand):
            found[cand] = f"container '{name}' (/app/data)"

    # 2. Walk likely roots, skipping the heavy directories.
    skip = {"node_modules", ".git", ".cache", ".npm", ".cargo", "AppData",
            "Library", "site-packages", "__pycache__", "venv", ".venv", "dist", "build"}
    roots = [os.path.expanduser("~")] + [os.path.expanduser(r) for r in (args.root or [])]
    for root in roots:
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            if dirpath.count(os.sep) - base_depth >= 4:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".git")]
            if "data.sqlite" in filenames and os.path.basename(dirpath) == "db":
                cand = os.path.join(dirpath, "data.sqlite")
                found.setdefault(cand, "filesystem")

    if not found:
        die("no data.sqlite found. Pass --root <dir> to search somewhere else, "
            "or use --db with a path you already know.")

    rows = []
    for path, origin in found.items():
        info = db_summary(path)
        if info:
            rows.append((info["cline"], info["total"], path, origin))
    if not rows:
        die("found data.sqlite file(s), but none has a providerConnections table")

    rows.sort(reverse=True)
    print(f"{len(rows)} candidate database(s):\n")
    for cline, total, path, origin in rows:
        print(f"  cline={cline:<4} total={total:<5} {path}")
        print(f"                        from: {origin}")
    best = rows[0]
    print(f"\nMost Cline accounts ({best[0]}) -> use this one:\n")
    print(f'  --db "{best[2]}"')


def mask(token: str, keep: int = 6) -> str:
    if not token:
        return "-"
    return f"{token[:keep]}...{token[-4:]}" if len(token) > keep + 8 else "***"


def export_rows(conn: sqlite3.Connection, only_email: str | None = None) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM providerConnections WHERE provider='cline' ORDER BY priority"
    ).fetchall()
    out = []
    for row in rows:
        data = json.loads(row["data"] or "{}")
        access = data.get("accessToken", "") or ""
        email = (row["email"] or data.get("email") or jwt_email(access) or "").strip()
        if only_email and only_email.strip().lower() not in email.lower():
            continue
        out.append({
            "id": row["id"],
            "email": email,
            "name": row["name"],
            "authType": row["authType"],
            "priority": row["priority"],
            "isActive": row["isActive"],
            "accessToken": access,
            "refreshToken": data.get("refreshToken", "") or "",
            "expiresAt": data.get("expiresAt"),
            "expiresIn": data.get("expiresIn"),
            "lastRefreshAt": data.get("lastRefreshAt"),
            "testStatus": data.get("testStatus"),
            "lastError": data.get("lastError"),
            "providerSpecificData": data.get("providerSpecificData") or {},
        })
    return out


def cmd_export(args):
    db_path = resolve_db_path(args)
    with connect(db_path) as conn:
        rows = export_rows(conn, args.email)
    if not rows:
        die("no matching cline connection to export")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = os.path.expanduser(
        args.out or f"~/vansrouter-tools/cline-export-{stamp}.json"
    )

    if args.format == "cpa":
        payload = [{
            "type": "cline",
            "email": r["email"],
            "accessToken": r["accessToken"],
            "refreshToken": r["refreshToken"],
            "expiresAt": r["expiresAt"],
            "providerSpecificData": r["providerSpecificData"],
        } for r in rows]
    elif args.format == "env":
        payload = None
        lines = []
        for i, r in enumerate(rows, 1):
            lines.append(f"# {r['email']}  (active={r['isActive']} test={r['testStatus']} exp={r['expiresAt'] or '-'})")
            lines.append(f"export CLINE_{i}_EMAIL={json.dumps(r['email'])}")
            lines.append(f"export CLINE_{i}_ACCESS_TOKEN={json.dumps(r['accessToken'])}")
            lines.append(f"export CLINE_{i}_REFRESH_TOKEN={json.dumps(r['refreshToken'])}")
        body = "\n".join(lines) + "\n"
    else:
        payload = rows

    if payload is not None:
        body = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    directory = os.path.dirname(out_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)

    print(f"DB    : {db_path}")
    print(f"export: {out_path}  ({len(rows)} connection, mode 0600)")
    print()
    for r in rows:
        flag = "expired" if _is_expired(r["expiresAt"]) else "ok"
        print(f"  [{r['priority']:>2}] {r['email']}")
        print(f"       access  : {mask(r['accessToken'])}")
        print(f"       refresh : {mask(r['refreshToken'])}")
        print(f"       token exp {r['expiresAt'] or '-'} ({flag})  accessTokenExpiresAt, not the refresh token")
        print(f"       test={r['testStatus']} active={r['isActive']} id={r['id']}")
        if args.show_tokens:
            print(f"       AT: {r['accessToken']}")
            print(f"       RT: {r['refreshToken']}")


def main():
    ap = argparse.ArgumentParser(
        description="Inject/list/verify/delete Cline OAuth credentials in a 9router-family DB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--instance", default="vansrouter", help="vansrouter | 9router (default: vansrouter)")
    ap.add_argument("--db", help="explicit path to data.sqlite (overrides --instance)")
    ap.add_argument("--port", type=int, help="gateway port for verify (default from --instance)")
    ap.add_argument("--no-backup", action="store_true", help="skip the pre-write DB backup")

    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="show cline connections (never prints tokens)")

    imp = sub.add_parser("import", help="write credentials into providerConnections")
    imp.add_argument("--cpa", action="store_true", help="read cline-*.json from the CPA auth store")
    imp.add_argument("--file", help="read a credential JSON (object or list)")
    imp.add_argument("--only", help="substring filter on the source file name (with --cpa)")
    imp.add_argument("--missing-only", action="store_true", help="skip emails already in this DB")
    imp.add_argument("--priority", type=int, help="explicit priority for new rows")
    imp.add_argument("--dry-run", action="store_true", help="parse and report, write nothing")
    imp.add_argument("--verify", action="store_true", help="run the app's connection test after writing")

    ver = sub.add_parser("verify", help="run the app's connection test (upstream users/me probe)")
    ver.add_argument("--email", help="only this email")

    dele = sub.add_parser("delete", help="remove a cline connection")
    dele.add_argument("--email")
    dele.add_argument("--id")

    exp = sub.add_parser("export", help="dump cline credentials to a 0600 file")
    exp.add_argument("--out", help="output path (default ~/vansrouter-tools/cline-export-<stamp>.json)")
    exp.add_argument("--format", choices=["json", "cpa", "env"], default="json",
                     help="json=full db rows, cpa=importable by --file, env=shell exports (default json)")
    exp.add_argument("--email", help="only rows whose email contains this substring")
    exp.add_argument("--show-tokens", action="store_true", help="print tokens to stdout too")

    fnd = sub.add_parser("find-db", help="locate candidate data.sqlite files (read-only)")
    fnd.add_argument("--root", action="append", help="extra directory to search (repeatable)")

    args = ap.parse_args()
    {"list": cmd_list, "import": cmd_import, "verify": cmd_verify,
     "delete": cmd_delete, "export": cmd_export, "find-db": cmd_find_db}[args.cmd](args)


if __name__ == "__main__":
    main()