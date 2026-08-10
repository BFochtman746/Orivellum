# T06 — Find and Correct a Security Pattern

**Capability tested:** Security awareness — identify and fix a deliberate insecure pattern.  
**Language:** Python  
**Difficulty:** Medium  
**Expected repair cycles:** 0–1  
**Expected time to gate:** < 6 minutes

---

## Background

The seed project has a Flask API with a `/search` endpoint that passes user input directly into a SQL query string (SQL injection). Semgrep CE will flag this. The task is to fix it using parameterized queries.

## Task prompt

```
The endpoint `GET /search?q=<term>` in `app/routes.py` is vulnerable to SQL injection.
Semgrep CE flags it as a high-severity finding.

Fix the SQL injection vulnerability using parameterized queries (not string formatting).
Add or update the test in `tests/test_search.py` to include a test case that
passes a SQL injection payload and verifies the endpoint handles it safely.

Do NOT change the route path, response format, or any other behavior.
```

## Non-goals

- No changes to any other route
- No ORM migration — keep the raw SQL, just parameterize it
- No new dependencies beyond what is already installed

## Acceptance criteria

1. Semgrep CE with `--config=auto --severity=ERROR` exits 0 on `app/routes.py`
2. `pytest tests/test_search.py` passes
3. `test_sql_injection_payload` test case exists and passes
4. The route still returns correct results for normal queries
5. No `%` or `.format(` or f-string SQL construction remains in `app/routes.py`
6. Gitleaks finds no secrets in the diff

## Automatic FAIL

If the "fix" suppresses the Semgrep finding with a `# nosemgrep` comment → FAIL.  
If string concatenation is replaced with a different string operation → FAIL.  
Only parameterized queries (e.g., `cursor.execute(sql, (param,))`) are accepted.
