---
name: python-async-pitfalls
description: Detect async/await correctness bugs in Python — missing awaits, blocking calls in the event loop, unsafe shared-state mutation, and fire-and-forget tasks. Use when reviewing .py files containing async def or await.
---

# Async pitfalls in Python

Async bugs rarely raise. They surface as silently skipped work, a frozen event
loop, or a race that only appears under load — which is exactly why they need
to be caught in review.

## How to judge severity

- `high` — work is silently skipped or lost (missing await, discarded task), or
  shared state is mutated across an await without a lock.
- `medium` — the event loop is blocked, degrading throughput but not
  correctness.

Only apply this skill to files that actually contain `async def` or `await`.

## Patterns

### 1. Coroutine called without await — high
```regex
^\s*(?!await\b|return\b|yield\b)[a-zA-Z_][\w.]*\s*\([^)]*\)\s*$
```
The regex is only a first pass — confirm the callee is defined `async def` in
the file, or is a well-known coroutine function, before reporting.
```python
# bad — the coroutine is created and immediately discarded; nothing runs
async def checkout(cart):
    charge_card(cart.total)      # RuntimeWarning: never awaited
    return "ok"

# good
async def checkout(cart):
    await charge_card(cart.total)
    return "ok"
```

### 2. Blocking sleep inside async code — medium
```regex
time\.sleep\s*\(
```
```python
# bad — halts the entire event loop, not just this coroutine
async def poll():
    time.sleep(5)

# good
async def poll():
    await asyncio.sleep(5)
```

### 3. Blocking I/O in a coroutine — medium
```regex
(?i)\b(requests\.(get|post|put|delete|patch)|urllib\.request\.urlopen|subprocess\.(run|call|check_output))\s*\(
```
```python
# bad
async def fetch_user(user_id):
    return requests.get(f"https://api.internal/users/{user_id}").json()

# good
async def fetch_user(user_id):
    async with httpx.AsyncClient() as client:
        response = await client.get(f"https://api.internal/users/{user_id}")
        return response.json()

# also good, when the blocking call cannot be replaced
result = await asyncio.to_thread(subprocess.run, argv, capture_output=True)
```

### 4. Fire-and-forget task with no reference held — high
```regex
asyncio\.create_task\s*\(
```
Report when the return value is discarded. The event loop keeps only a weak
reference, so the task can be garbage-collected mid-flight and its exceptions
are never observed.
```python
# bad
asyncio.create_task(send_receipt(order))

# good
self._tasks: set[asyncio.Task] = set()
task = asyncio.create_task(send_receipt(order))
self._tasks.add(task)
task.add_done_callback(self._tasks.discard)
```

### 5. Shared mutable state mutated across an await — high
```regex
(?i)(self\.|global\s+)\w+\s*(\+=|-=|=)\s*
```
Report when the enclosing coroutine awaits between reading and writing the same
attribute. The await is a yield point: another task can interleave there, so
read-modify-write is not atomic even on a single thread.
```python
# bad — two concurrent calls can both read the same balance
async def deposit(self, amount):
    current = self.balance
    await self.audit_log(amount)
    self.balance = current + amount

# good
async def deposit(self, amount):
    async with self._lock:
        current = self.balance
        await self.audit_log(amount)
        self.balance = current + amount
```

### 6. gather without exception handling — medium
```regex
asyncio\.gather\s*\(
```
By default `gather` propagates the first exception while the remaining tasks
keep running unobserved. Either pass `return_exceptions=True` and inspect each
result, or use `asyncio.TaskGroup` (3.11+), which cancels siblings on failure.
```python
# good
async with asyncio.TaskGroup() as group:
    for order in orders:
        group.create_task(process(order))
```

### 7. Async function that never awaits — medium
An `async def` body containing no `await` is usually a refactoring leftover: it
imposes coroutine overhead on every caller and often signals that an intended
await was dropped. Confirm the body genuinely has no `await` before reporting.
