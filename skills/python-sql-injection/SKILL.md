---
name: python-sql-injection
description: Detect unsafe SQL query construction in Python — f-strings, concatenation, and % formatting reaching a database cursor. Use when reviewing .py files that touch a database or ORM.
---

# SQL injection in Python

The rule is simple: **values never go into query text.** They go in as bound
parameters. Any query string built with an f-string, `+`, `%`, or `.format()`
that contains a variable is a finding.

## How to judge severity

- `critical` — interpolated value is plainly request-derived (`request.`,
  `args`, `form`, `json`, `params`, a route handler argument).
- `high` — interpolation of a variable whose origin is not visible in the file.
- `medium` — interpolation of a value that is provably a local literal or
  constant, but the pattern is still fragile and should be parameterized.

## What is NOT a finding

Placeholders inside the string are correct usage, not interpolation:
`cursor.execute("SELECT * FROM t WHERE id = %s", (user_id,))` and
`cursor.execute("... WHERE id = ?", (user_id,))` are both safe. Identifiers
(table and column names) cannot be bound as parameters — when those are
interpolated, the correct fix is an allowlist, not a bound parameter.

## Patterns

### 1. f-string query passed to a cursor — critical
```regex
(?i)\.(execute|executemany|executescript)\s*\(\s*f["']
```
```python
# bad
cursor.execute(f"SELECT * FROM users WHERE email = '{email}'")

# good
cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
```

### 2. f-string SQL assigned then executed — critical
```regex
(?i)(query|sql|stmt|statement)\s*=\s*f["'].*(select|insert|update|delete|drop)
```
```python
# bad
query = f"DELETE FROM sessions WHERE token = '{token}'"
cursor.execute(query)

# good
cursor.execute("DELETE FROM sessions WHERE token = %s", (token,))
```
Splitting construction from execution across two lines hides nothing — anchor
the finding on the line where the string is built.

### 3. String concatenation into a query — high
```regex
(?i)["'](select|insert|update|delete)[^"']*["']\s*\+
```
```python
# bad
cursor.execute("SELECT * FROM orders WHERE customer = '" + customer_id + "'")

# good
cursor.execute("SELECT * FROM orders WHERE customer = %s", (customer_id,))
```

### 4. Percent formatting applied to the query string — high
```regex
(?i)\.(execute|executemany)\s*\(\s*["'][^"']*%s[^"']*["']\s*%
```
```python
# bad — the % applies to the string, so nothing is ever bound
cursor.execute("SELECT * FROM t WHERE id = %s" % user_id)

# good — the driver binds the parameter
cursor.execute("SELECT * FROM t WHERE id = %s", (user_id,))
```
This is the most commonly missed variant, because the safe and unsafe forms
differ only by a comma versus a `%`.

### 5. `.format()` on a query string — high
```regex
(?i)["'][^"']*(select|insert|update|delete)[^"']*["']\.format\s*\(
```

### 6. Raw SQL through an ORM escape hatch — high
```regex
(?i)\.(raw|execute|text)\s*\(\s*f["']|text\s*\(\s*f["']
```
```python
# bad
db.session.execute(text(f"UPDATE accounts SET balance = {amount} WHERE id = {account_id}"))

# good
db.session.execute(
    text("UPDATE accounts SET balance = :amount WHERE id = :id"),
    {"amount": amount, "id": account_id},
)
```
An ORM in the file does not make the code safe — `raw()`, `text()`, and
`execute()` all bypass it.

### 7. Interpolated table or column identifier — medium
```regex
(?i)(from|join|into|update)\s+\{|(order\s+by|group\s+by)\s+\{
```
```python
# bad
cursor.execute(f"SELECT * FROM users ORDER BY {sort_column}")

# good — identifiers cannot be bound, so use an allowlist
ALLOWED_SORTS = {"created_at", "email", "id"}
if sort_column not in ALLOWED_SORTS:
    raise ValueError(f"invalid sort column: {sort_column}")
cursor.execute(f"SELECT * FROM users ORDER BY {sort_column}")
```
