---
name: python-secret-patterns
description: Detect hardcoded credentials in Python — passwords, API keys, AWS keys, private keys, JWT secrets, and database connection strings. Use when reviewing any .py file.
---

# Hardcoded secrets in Python

A committed credential is compromised the moment it lands in git history, and
stays compromised after the line is deleted. Treat any literal credential as at
least `high`, and `critical` when the value looks production-grade.

## How to judge severity

- `critical` — the value looks real and production-scoped (`sk-prod-`, `AKIA`,
  a full private key block, a connection string with a live host).
- `high` — a real-looking literal secret with unclear scope.
- `medium` — a suspicious literal that may be a placeholder.

## What is NOT a finding

Do not report a line when the value comes from the environment or a vault, or
when it is transparently a placeholder. Check for these before flagging:

`os.environ`, `os.getenv`, `settings.`, `config.`, `Secret`, `vault`,
`getpass`, `<...>`, `xxx`, `changeme`, `example`, `dummy`, `fake`, `test`,
`placeholder`, `your-`, `redacted`.

## Patterns

### 1. Hardcoded password assignment — high
```regex
(?i)\b(password|passwd|pwd)\s*=\s*["'][^"']{4,}["']
```
```python
# bad
conn = psycopg2.connect(host="db.internal", user="app", password="Sup3rS3cret!")

# good
conn = psycopg2.connect(host=os.environ["DB_HOST"], user=os.environ["DB_USER"],
                        password=os.environ["DB_PASSWORD"])
```

### 2. API key / token assignment — high
```regex
(?i)\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|secret[_-]?key)\s*=\s*["'][^"']{8,}["']
```
```python
# bad
OPENAI_API_KEY = "sk-proj-REDACTEDEXAMPLEREDACTEDEXAMPLE"

# good
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
```

### 3. Provider-prefixed key literal — critical
```regex
(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36}|gho_[A-Za-z0-9]{36}|xox[baprs]-[A-Za-z0-9-]{10,})
```
An OpenAI, GitHub, or Slack token matched by prefix is unambiguous — there is
no placeholder that looks like this. Report `critical` and recommend immediate
revocation, not just removal.

### 4. AWS access key id — critical
```regex
\b(A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b
```
```python
# bad
AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

# good — let boto3 resolve the default credential chain
session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE"))
```

### 5. AWS secret access key — critical
```regex
(?i)aws_secret_access_key\s*=\s*["'][A-Za-z0-9/+=]{40}["']
```

### 6. Private key block — critical
```regex
-----BEGIN (RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----
```
An embedded private key is never acceptable in source. Load it from a mounted
secret file or a key management service.

### 7. JWT signing secret — high
```regex
(?i)\b(jwt[_-]?secret|signing[_-]?key|session[_-]?secret)\s*=\s*["'][^"']{6,}["']
```
```python
# bad
app.config["JWT_SECRET"] = "dev-secret-key"

# good
app.config["JWT_SECRET"] = os.environ["JWT_SECRET"]
```
A weak or shared signing secret lets anyone mint valid tokens for any user, so
this is an authentication bypass, not just an exposure.

### 8. Database connection string with inline credentials — critical
```regex
(?i)(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp)://[^\s:'"]+:[^\s@'"]+@
```
```python
# bad
DATABASE_URL = "postgresql://app:hunter2@prod-db.internal:5432/orders"

# good
DATABASE_URL = os.environ["DATABASE_URL"]
```

### 9. Hardcoded bearer / basic authorization header — high
```regex
(?i)["'](Bearer|Basic)\s+[A-Za-z0-9+/=_.-]{16,}["']
```

### 10. Encryption key or salt literal — high
```regex
(?i)\b(encryption[_-]?key|cipher[_-]?key|salt|iv)\s*=\s*(b?["'][^"']{8,}["'])
```
A fixed salt or IV defeats the purpose of the primitive: identical plaintexts
produce identical ciphertexts, and precomputed tables become viable. Generate
per-record with `os.urandom` / `secrets.token_bytes`.
