---
name: generic-secret-patterns
description: Detect hardcoded credentials across non-Python files — YAML, .env, Dockerfiles, shell scripts, CI configs, JSON, and Terraform. Use when reviewing any non-Python file.
---

# Cross-language secret patterns

Config files leak credentials more often than source files do, because they
look like the place secrets are supposed to live. They are not — a committed
config is as public as committed code.

## How to judge severity

- `critical` — a provider-shaped token or a private key block.
- `high` — a literal credential assignment with a real-looking value.
- `medium` — a suspicious literal that could be a placeholder.

## What is NOT a finding

Skip lines whose value is a reference or an obvious placeholder:
`${VAR}`, `$VAR`, `{{ ... }}`, `!secret`, `secretKeyRef`, `valueFrom`,
`vault:`, `<...>`, `changeme`, `example`, `dummy`, `xxx`, `your-`, `redacted`.
A `.env.example` or `*.sample` file full of placeholders is correct practice.

## Patterns

### 1. Credential assignment in an env or config file — high
```regex
(?i)^\s*[A-Z0-9_]*(PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY)[A-Z0-9_]*\s*[:=]\s*\S+
```
```bash
# bad — .env committed to the repository
DB_PASSWORD=hunter2
STRIPE_SECRET_KEY=sk_live_REDACTEDEXAMPLE00000000

# good — .env.example, with .env in .gitignore
DB_PASSWORD=
STRIPE_SECRET_KEY=
```
A committed `.env` is a finding on its own, independent of the values in it.

### 2. Dockerfile ENV or ARG carrying a secret — high
```regex
(?i)^\s*(ENV|ARG)\s+\w*(PASSWORD|SECRET|TOKEN|KEY)\w*[\s=]+\S+
```
```dockerfile
# bad — baked into a layer and readable via `docker history` forever
ENV DATABASE_PASSWORD=prod-secret-9f2a

# good — provide at runtime, or mount as a build secret
RUN --mount=type=secret,id=db_password \
    DATABASE_PASSWORD=$(cat /run/secrets/db_password) ./migrate.sh
```
Note that `ARG` is not safer than `ENV`: both persist in the image history.

### 3. Provider-shaped token literal — critical
```regex
(sk_live_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{36}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35})
```

### 4. AWS key material in config — critical
```regex
\b(AKIA|ASIA)[A-Z0-9]{16}\b|(?i)aws_secret_access_key\s*[:=]\s*\S{40}
```

### 5. Private key block — critical
```regex
-----BEGIN (RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----
```

### 6. Connection string with inline credentials — critical
```regex
(?i)(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp|https?)://[^\s:'"]+:[^\s@'"]+@
```
```yaml
# bad
services:
  api:
    environment:
      DATABASE_URL: "postgresql://app:hunter2@prod-db:5432/orders"

# good
services:
  api:
    environment:
      DATABASE_URL: ${DATABASE_URL:?DATABASE_URL is required}
```

### 7. Kubernetes Secret with inline data — high
```regex
(?i)kind:\s*Secret|^\s*(data|stringData):\s*$
```
Base64 in a `data:` block is encoding, not encryption — anyone with the
manifest has the credential. Use a sealed-secret or external-secret operator,
or `secretKeyRef` against a secret provisioned outside the repository.

### 8. Hardcoded credential in a CI workflow — high
```regex
(?i)(password|token|api[_-]?key|secret)\s*[:=]\s*["']?[A-Za-z0-9+/=_-]{12,}
```
```yaml
# bad
- run: curl -H "Authorization: Bearer ghp_REDACTEDEXAMPLEREDACTEDEXAMPLE0001"

# good
- run: curl -H "Authorization: Bearer $GITHUB_TOKEN"
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

### 9. Basic-auth credentials in a URL — high
```regex
(?i)https?://[^\s/'"]+:[^\s@'"]+@[^\s'"]+
```

### 10. Terraform variable with a literal default secret — high
```regex
(?i)default\s*=\s*["'][^"']{8,}["']
```
Report when the surrounding `variable` block names a password, token, or key.
Mark such variables `sensitive = true` and supply the value from a tfvars file
or a secret manager, never as a default in committed HCL.
