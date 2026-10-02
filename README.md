# cline-cred — install Cline accounts into 9router / VansRouter

One Python script that writes Cline (WorkOS OAuth) account credentials straight into a
9router or VansRouter database. No browser, no clicking, no restart.

**Zero dependencies.** Standard library only.

---

## FOR AI AGENTS — start here

If you are an agent asked to install a Cline account for a user, run this sequence.
Do not guess. If step 2 fails, follow section 2.

```bash
# 1. Make sure Python exists (3.8+). On Windows use "python", not "python3".
python --version

# 2. Locate the app's database. The path differs on every machine, so never guess.
python cline_cred.py find-db
#    The output prints one line: --db "...". Use that in every command below.
#    If it finds nothing, try --instance 9router (works when the container is named 9router).

# 3. Install the account. Replace account.json with the credential file you were given.
python cline_cred.py --instance 9router import --file account.json --verify

# 4. Prove it worked. Replace with the email from the credential file.
python cline_cred.py --instance 9router verify --email name@domain.com
```

**You are done when step 4 prints `PASS`.**

If step 2 prints `DB not found` or reports `0 cline connection(s)` when an account should
be there, go to **section 2**. That is the cause of 90% of failures.

---

## 1. Requirements

| | |
| --- | --- |
| Python | **3.8 or newer**. Check: `python --version` |
| App | 9router or VansRouter, **and it must be running** (for the verify step) |
| Docker | Optional. Only needed for automatic database detection |
| OS | Windows, macOS, Linux — all supported |

No `pip install` of anything.

**How to run commands:**

| OS | Command |
| --- | --- |
| Windows | `python cline_cred.py ...` |
| macOS / Linux | `python3 cline_cred.py ...` |

This document writes `python cline_cred.py` everywhere. On macOS/Linux, use `python3`.

---

## 2. Locating the database — the step that usually fails

The script has to know where `data.sqlite` lives. **The path is different on every machine**,
so no default can be trusted. Four ways, in order:

### Way A — let the script search for it (start here)

```bash
python cline_cred.py find-db
```

The script inspects every Docker container that mounts something at `/app/data`, then walks
your home folder, and reports every `data.sqlite` that has a `providerConnections` table:

```
3 candidate database(s):

  cline=2    total=42    /home/user/.vansrouter/db/data.sqlite
                        from: container 'vansrouter' (/app/data)
  cline=0    total=13    /home/user/.9router/db/data.sqlite
                        from: filesystem

Most Cline accounts (2) -> use this one:

  --db "/home/user/.vansrouter/db/data.sqlite"
```

This command is **read-only** — it writes nothing. If your database is not found, add its
location:

```bash
python cline_cred.py find-db --root "D:\apps\9router"
```

### Way B — automatic via Docker

If the app runs in Docker and the container is named `9router` or `vansrouter`, the path is
resolved automatically:

```bash
python cline_cred.py --instance 9router list
python cline_cred.py --instance vansrouter list
```

Check the container name if unsure:

```bash
docker ps --format "{{.Names}}"
```

**If the container is named something else** (e.g. `9router-app`, `myrouter`), this falls
back to a default path that is probably wrong. Use way A or C instead.

### Way C — point at it directly with `--db` (most reliable)

```bash
python cline_cred.py --db "C:\path\to\data.sqlite" list
python cline_cred.py --db /path/to/data.sqlite list
```

If you already know the location, this is the fastest and least error-prone route.

### Way D — search for it yourself

```bash
# Windows (PowerShell)
Get-ChildItem -Path C:\ -Filter data.sqlite -Recurse -ErrorAction SilentlyContinue

# Windows with Docker Desktop
docker inspect <container-name> --format "{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}"

# macOS / Linux
find / -name data.sqlite -not -path "*/node_modules/*" 2>/dev/null
```

You are looking for `<data-dir>/db/data.sqlite` — a file inside a folder named `db`.

### Once found, keep using it

Add `--db` to **every** command with the same path:

```bash
python cline_cred.py --db "C:\Users\budi\9router\data\db\data.sqlite" import --file account.json --verify
```

**If `list` shows the accounts you expect, everything else will work too.**

---

## 3. Installing the account (import)

```bash
python cline_cred.py --instance 9router import --file account.json --verify
```

What happens:

1. The script reads `account.json` and validates it.
2. It **automatically backs up** the database to `<data-dir>/db/backups/` before writing.
3. It writes the credential row into the `providerConnections` table.
4. With `--verify`, the app itself tests the credential against Cline and reports the result.

**No restart needed.** The app reads `providerConnections` from SQLite on every request, so
the account shows up in the dashboard immediately and is available for routing right away.

### Safe to run repeatedly

Running the same command twice **will not create a duplicate**. Deduplication is keyed on
`email`:

- email already present → the existing row is **updated**
- email not present → a **new** row is added

### Other `import` options

| Option | What it does |
| --- | --- |
| `--file account.json` | Read from a file (single object or a list) |
| `--verify` | Test against upstream right after writing |
| `--dry-run` | Parse and report only — writes nothing |
| `--missing-only` | Skip emails already present in the database |
| `--priority 5` | Set the account priority manually |
| `--no-backup` | Skip the automatic backup (not recommended) |
| `--cpa` | Read from CPA's `cline-*.json` files (requires Docker) |

Example — check first without writing:

```bash
python cline_cred.py --instance 9router import --file account.json --dry-run
```

### An expired access token in the file is fine

The credential file carries two tokens with very different lifetimes.

| | Access token | Refresh token |
| --- | --- | --- |
| Lifetime | about 1 hour | no visible expiry |
| Job | makes API requests | mints new access tokens |
| Changes? | replaced constantly | never rotates |

By the time you import the file, its access token may already be expired. **That does not
matter.** The refresh token is the durable credential, and it is the one the tool actually
needs. A file that sat in a folder for a week installs exactly as cleanly as a fresh one.

What happens on import:

1. The script records how many seconds the access token has left. An already-expired token is
   recorded as `0`.
2. The app sees the stale expiry and refreshes on first use, using the refresh token.
3. From then on it keeps refreshing by itself, roughly once an hour.

You can trigger the refresh deliberately:

```bash
python cline_cred.py --instance 9router import --file account.json --verify
```

`refreshed=true` in the output means this path ran and worked. It is a **stronger** result than
`refreshed=false`, because it proves the refresh token is live.

**The one thing that must be valid is the refresh token.** If it has been revoked, nothing can
recover the account — see the `401` row in Troubleshooting.

Verified against a live Cline account: exchanging its refresh token directly at
`POST https://api.cline.bot/api/v1/auth/refresh` returned a fresh access token and the
**same** refresh token back, unchanged.

---

## 4. Verify — do not skip this

```bash
python cline_cred.py --instance 9router verify --email name@domain.com
```

Expected output:

```
PASS name@domain.com    valid=true refreshed=False
```

This means the app successfully used the credential to call Cline.

- `valid=true` — the credential is alive and accepted upstream
- `refreshed=false` — the access token was still fresh, no refresh needed
- `refreshed=true` — the access token had expired and was **successfully refreshed** using
  the refresh token. This is also a good result; it proves the refresh token works.

`--email` accepts a **substring**, not just the full address:

```bash
python cline_cred.py --instance 9router verify --email budi
```

**Verify requires the app to be running**, because the script calls the app's own test
endpoint on `127.0.0.1`. If the app is down, you get a connection failure.

---

## 5. Other commands

### Inspect the database

```bash
python cline_cred.py --instance 9router list
```

```
DB: /data/db/data.sqlite
2 cline connection(s)

  [ 1] name@domain.com
       authType=oauth active=1 test=active RT exp=2026-10-02T15:20:12.130Z (ok)
       id=00000000-1111-2222-3333-444444444444
```

- `RT` — has a refresh token (required; without it the account dies within an hour)
- `exp` — expiry of the **access token**, not the refresh token. Being in the past is normal.
- `active=1` — the account is enabled and eligible for routing
- `test=active` — last known test status

### Find the database (see also section 2)

```bash
python cline_cred.py find-db
python cline_cred.py find-db --root "D:\apps\9router"
```

Read-only. Reports every `data.sqlite` that has a `providerConnections` table, with the
Cline account count for each, then suggests one `--db` line to use.

### Export credentials

```bash
python cline_cred.py --instance 9router export
python cline_cred.py --instance 9router export --format cpa --out handover.json
```

Formats: `json` (full dump), `cpa` (compact, for handing to someone else), `env` (shell
variables). Files are written with restricted permissions (0600) — **this has no effect on
Windows**, see section 6.

### Delete an account

```bash
python cline_cred.py --instance 9router delete --email name@domain.com
```

An automatic backup is still taken before deleting.

---

## 6. Windows notes

The script runs on Windows, but four things differ:

**1. The command is `python`, not `python3`.**

**2. File permissions are not protected.** The script writes export files with mode `0600`
(owner-only). Windows does not use mode bits — permissions come from ACLs. It will not error,
but the file is **not automatically protected**. If you store credential files on Windows,
keep them in a private folder and never in a synced folder (OneDrive, Dropbox, Google Drive).

**3. The database usually lives inside Docker.** Most Windows users run 9router/VansRouter
through Docker Desktop, in which case way A in section 2 works — as long as the container is
named `9router` or `vansrouter`.

**4. Quote your paths.** Windows paths contain spaces and backslashes:

```bash
python cline_cred.py --db "C:\Users\budi\9router\data\db\data.sqlite" list
```

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `DB not found: <path>` | Wrong database location, or the app has never run | Section 2. Pass `--db` with the correct path |
| `0 cline connection(s)` when accounts should exist | **Reading a different app's database.** The default path points at `<home>/.9router` — if you have more than one install, it opens the wrong one | Check the `DB:` line in the output. Make sure it points at the install you actually use |
| `no such object: 9router` | Container has a different name, or Docker is not running | `docker ps --format "{{.Names}}"`, then use `--db` |
| `credential ... has no email and no JWT claim` | Credential file has no email, and the token is not a JWT | Request a correct credential file from whoever supplied it |
| `FAIL ... connection refused` during `verify` | The app is not running | Start 9router/VansRouter first |
| `FAIL ... 401` during `verify` | Credential is dead / refresh token revoked | Contact whoever supplied the credential |
| `FAIL ... 404` during `verify` | Wrong `--port` | Add the correct `--port <port>` |
| Import succeeds but the account is missing from the dashboard | The app is reading a different database | Check the `DB:` line — make sure it matches the install you use |
| `no data.sqlite found` from `find-db` | Database is outside your home folder | Add `--root <dir>`, or use `--db` with a path you know |
| `found data.sqlite file(s), but none has a providerConnections table` | Unrelated `data.sqlite` files were found | Use `--db` to point at the right one |
| `SyntaxError` | Python too old | Needs 3.8+. Check `python --version` |

**Universal escape hatch — these two commands resolve almost every case:**

```bash
python cline_cred.py find-db                          # find the right database
python cline_cred.py --db "<result above>" list       # confirm it points at the right one
```

If `list` shows the Cline accounts you expect, every other command will work.

---

## 8. Why this script is necessary

Cline in 9router/VansRouter is **OAuth-only**. The `cline` provider does not declare
`authModes`, so:

- `POST /api/providers` and `/api/providers/bulk` reject it with **400 `Invalid provider`**
- There is no token-import endpoint for Cline (only codex/cursor/kiro/gitlab/grok-cli/iflow have one)
- The only official path is the browser flow, which requires pasting an *authorization code*

There is a gap in `POST /api/oauth/cline/exchange` that accepts a raw JWT, **but** that route
stores `authType:"access_token"` with **no refresh token** — the connection dies as soon as
the WorkOS access token expires, roughly **one hour** later.

This script writes exactly the same row the dashboard OAuth flow would write, **including the
refresh token**, directly into `providerConnections`. That is why the account keeps working
long-term instead of dying after an hour.

---

## 9. Credential file format

The credential file is JSON. Two shapes are accepted.

**Compact shape (recommended):**

```json
[
  {
    "type": "cline",
    "email": "name@domain.com",
    "accessToken": "eyJhbGciOi...",
    "refreshToken": "HR0bHGRP...",
    "expiresAt": "2026-10-02T15:20:12.130Z",
    "providerSpecificData": { "firstName": "", "lastName": "" }
  }
]
```

**Database dump shape** (also accepted; extra fields such as `id`, `priority`, `isActive`,
and `testStatus` are simply ignored on import).

A single object without the surrounding `[ ]` works too.

**Required:** `email` (or a token that carries an email claim) and `refreshToken`. Without a
`refreshToken`, the account dies within an hour. The `accessToken` may already be expired when
you import it — that is normal and harmless, see section 3.

**Note:** the `name` field in the file is **ignored**. When creating a new row, the display
name is always set to the email. To use a different display name, change it after importing:

```sql
UPDATE providerConnections SET name='Account-01' WHERE email='name@domain.com';
```

`name` is a display label only — it does not affect routing. Routing is decided by
`priority`, `isActive`, and test status.

---

## 10. Database schema (reference)

Verified on VansRouter 0.91.33, schemaVersion 8.

```
providerConnections(
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, authType TEXT NOT NULL,
  name TEXT, email TEXT, priority INTEGER, isActive INTEGER DEFAULT 1,
  data TEXT NOT NULL,          -- JSON blob, everything that is not a column
  createdAt TEXT NOT NULL, updatedAt TEXT NOT NULL
)
```

Contents of `data` for a Cline OAuth row:

```
accessToken   JWT (with or without the "workos:" prefix — both are valid)
refreshToken  opaque, 25 characters
expiresAt     ACCESS token expiry, not the refresh token
expiresIn     seconds remaining
lastRefreshAt
testStatus    "active"
backoffLevel  0
providerSpecificData {firstName, lastName}
```

About the `workos:` prefix: the dashboard OAuth flow stores a **raw** JWT, while importing
through this script stores a **prefixed** one. Both are correct — the app normalises at call
time in `open-sse/shared/clineAuth.js` (`getClineAccessToken()` adds the prefix when missing).
Do not "fix" either of them.

---

## 11. Safety

- **Automatic backup** to `<data-dir>/db/backups/` before every write (unless `--no-backup`).
- **Never prints token values**, unless you explicitly ask with `--show-tokens`.
- **Deduplicated by email**, same as the app — re-running does not duplicate rows.

**A credential file is equivalent to a password.** Anyone holding it can use that account.
Do not put it in a git repository, do not send it over a public channel, and do not leave it
lying around in a temp folder.
