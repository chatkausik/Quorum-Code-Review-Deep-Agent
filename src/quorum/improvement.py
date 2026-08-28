"""Durable feedback and health signals for Quorum's improvement loop."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from quorum.config import IMPROVEMENT_DB
from quorum.models import PostResult, ReviewComment, ReviewResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS review_runs (
  id TEXT PRIMARY KEY,
  repository TEXT NOT NULL,
  pr_number INTEGER NOT NULL,
  head_sha TEXT NOT NULL,
  profile TEXT NOT NULL,
  created_at TEXT NOT NULL,
  files_expected INTEGER NOT NULL,
  files_reviewed INTEGER NOT NULL,
  finding_count INTEGER NOT NULL,
  llm_calls INTEGER NOT NULL,
  cost_usd REAL NOT NULL,
  budget_exceeded INTEGER NOT NULL,
  error TEXT,
  health_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS finding_decisions (
  run_id TEXT NOT NULL,
  finding_id TEXT NOT NULL,
  path TEXT NOT NULL,
  line INTEGER NOT NULL,
  severity TEXT NOT NULL,
  category TEXT NOT NULL,
  confidence INTEGER NOT NULL,
  title TEXT,
  anchor_hash TEXT NOT NULL,
  decision TEXT NOT NULL,
  reason TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (run_id, finding_id),
  FOREIGN KEY (run_id) REFERENCES review_runs(id)
);

CREATE TABLE IF NOT EXISTS evaluation_cases (
  run_id TEXT NOT NULL,
  finding_id TEXT NOT NULL,
  label TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (run_id, finding_id, label),
  FOREIGN KEY (run_id) REFERENCES review_runs(id)
);

CREATE TABLE IF NOT EXISTS improvement_issues (
  fingerprint TEXT PRIMARY KEY,
  repository TEXT NOT NULL,
  invariant TEXT NOT NULL,
  severity TEXT NOT NULL,
  summary TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  occurrences INTEGER NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  latest_run_id TEXT NOT NULL,
  evidence_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS improvement_repo_status
ON improvement_issues(repository, status, last_seen);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def finding_id(comment: ReviewComment) -> str:
    """Stable identity without retaining source or model prose."""
    raw = f"{comment.path}:{comment.line}:{comment.category}:{comment.anchor_text.strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _anchor_hash(comment: ReviewComment) -> str:
    return hashlib.sha256(comment.anchor_text.strip().encode()).hexdigest()[:16]


def _issue_id(repository: str, invariant: str) -> str:
    return hashlib.sha256(f"{repository}:{invariant}".encode()).hexdigest()[:20]


def _safe_error_label(error: str | None) -> str | None:
    """Retain failure class, not provider text that may echo reviewed input."""
    if not error:
        return None
    prefix = error.split(":", 1)[0].strip()
    return prefix[:80] if prefix.replace("_", "").isalnum() else "review_error"


class ImprovementStore:
    """SQLite store for review outcomes, feedback, and recurring failures."""

    def __init__(self, path: str | Path = IMPROVEMENT_DB) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def record_review(self, result: ReviewResult) -> None:
        created_at = _now()
        health_json = json.dumps(
            [check.model_dump(mode="json") for check in result.health_checks]
        )
        with self._conn() as conn:
            is_new_run = conn.execute(
                "SELECT 1 FROM review_runs WHERE id=?", (result.run_id,)
            ).fetchone() is None
            conn.execute(
                "INSERT INTO review_runs "
                "(id, repository, pr_number, head_sha, profile, created_at, "
                "files_expected, files_reviewed, finding_count, llm_calls, cost_usd, "
                "budget_exceeded, error, health_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "repository=excluded.repository, pr_number=excluded.pr_number, "
                "head_sha=excluded.head_sha, profile=excluded.profile, "
                "files_expected=excluded.files_expected, "
                "files_reviewed=excluded.files_reviewed, "
                "finding_count=excluded.finding_count, llm_calls=excluded.llm_calls, "
                "cost_usd=excluded.cost_usd, budget_exceeded=excluded.budget_exceeded, "
                "error=excluded.error, health_json=excluded.health_json",
                (
                    result.run_id,
                    result.context.full_repo,
                    result.context.pr_number,
                    result.context.head_sha,
                    result.profile,
                    created_at,
                    result.expected_files,
                    result.files_reviewed,
                    len(result.comments),
                    result.llm_calls,
                    result.total_cost_usd,
                    int(result.budget_exceeded),
                    _safe_error_label(result.error),
                    health_json,
                ),
            )
            # Re-persisting a run must not inflate occurrences or reopen an
            # issue a human closed -- but it must still record a failure this
            # store has never seen, so recurrence is a parameter of one upsert
            # rather than a separate branch that can only UPDATE.
            recurrence = 1 if is_new_run else 0
            for check in result.health_checks:
                if check.passed:
                    continue
                fingerprint = _issue_id(result.context.full_repo, check.name)
                conn.execute(
                    "INSERT INTO improvement_issues "
                    "(fingerprint, repository, invariant, severity, summary, status, "
                    "occurrences, first_seen, last_seen, latest_run_id, evidence_json) "
                    "VALUES (?,?,?,?,?,'open',1,?,?,?,?) "
                    "ON CONFLICT(fingerprint) DO UPDATE SET "
                    "severity=excluded.severity, summary=excluded.summary, "
                    "occurrences=improvement_issues.occurrences + ?, "
                    "last_seen=excluded.last_seen, latest_run_id=excluded.latest_run_id, "
                    "evidence_json=excluded.evidence_json, "
                    "status=CASE "
                    "WHEN improvement_issues.status='muted' THEN 'muted' "
                    "WHEN ? = 0 THEN improvement_issues.status "
                    "ELSE 'open' END",
                    (
                        fingerprint,
                        result.context.full_repo,
                        check.name,
                        check.severity,
                        check.detail,
                        created_at,
                        created_at,
                        result.run_id,
                        json.dumps(check.evidence),
                        recurrence,
                        recurrence,
                    ),
                )

    def record_decisions(
        self,
        result: ReviewResult,
        selected: list[ReviewComment],
        *,
        rejection_reason: str | None = None,
    ) -> None:
        selected_ids = {finding_id(comment) for comment in selected}
        now = _now()
        with self._conn() as conn:
            for comment in result.comments:
                fid = finding_id(comment)
                decision = "approved" if fid in selected_ids else "rejected"
                reason = None if decision == "approved" else rejection_reason
                self._upsert_decision(conn, result, comment, decision, reason, now)
                self._upsert_case(conn, result, comment, decision, now)

    def record_post_result(
        self,
        result: ReviewResult,
        selected: list[ReviewComment],
        post: PostResult,
    ) -> None:
        posted = set(post.posted_locations)
        now = _now()
        with self._conn() as conn:
            for comment in selected:
                location = f"{comment.path}:{comment.line}"
                decision = "posted" if location in posted else "postability_failure"
                reason = None if decision == "posted" else "anchor or diff validation failed"
                self._upsert_decision(conn, result, comment, decision, reason, now)
                self._upsert_case(conn, result, comment, decision, now)

    @staticmethod
    def _upsert_decision(
        conn: sqlite3.Connection,
        result: ReviewResult,
        comment: ReviewComment,
        decision: str,
        reason: str | None,
        now: str,
    ) -> None:
        conn.execute(
            "INSERT INTO finding_decisions "
            "(run_id, finding_id, path, line, severity, category, confidence, title, "
            "anchor_hash, decision, reason, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id, finding_id) DO UPDATE SET "
            "title=NULL, decision=excluded.decision, reason=excluded.reason, "
            "updated_at=excluded.updated_at",
            (
                result.run_id,
                finding_id(comment),
                comment.path,
                comment.line,
                comment.severity,
                comment.category,
                comment.confidence,
                None,
                _anchor_hash(comment),
                decision,
                reason,
                now,
            ),
        )

    @staticmethod
    def _upsert_case(
        conn: sqlite3.Connection,
        result: ReviewResult,
        comment: ReviewComment,
        label: str,
        now: str,
    ) -> None:
        # Human decisions and posting outcomes are two independent dimensions.
        # Within each dimension, however, labels are mutually exclusive: a
        # finding that a reviewer changes from rejected to approved must not
        # remain both a positive and a negative evaluation example.
        dimensions = {
            "approved": ("approved", "rejected"),
            "rejected": ("approved", "rejected"),
            "posted": ("posted", "postability_failure"),
            "postability_failure": ("posted", "postability_failure"),
        }
        exclusive = dimensions.get(label)
        if exclusive:
            placeholders = ",".join("?" for _ in exclusive)
            conn.execute(
                "DELETE FROM evaluation_cases WHERE run_id=? AND finding_id=? "
                f"AND label IN ({placeholders})",
                (result.run_id, finding_id(comment), *exclusive),
            )
        payload = {
            "repository": result.context.full_repo,
            "head_sha": result.context.head_sha,
            "path": comment.path,
            "line": comment.line,
            "severity": comment.severity,
            "category": comment.category,
            "confidence": comment.confidence,
            "anchor_hash": _anchor_hash(comment),
        }
        conn.execute(
            "INSERT OR REPLACE INTO evaluation_cases "
            "(run_id, finding_id, label, payload_json, created_at) VALUES (?,?,?,?,?)",
            (result.run_id, finding_id(comment), label, json.dumps(payload), now),
        )

    def list_issues(
        self, repository: str, *, status: str = "open"
    ) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM improvement_issues WHERE repository=? AND status=? "
                "ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
                "WHEN 'medium' THEN 2 ELSE 3 END, last_seen DESC",
                (repository, status),
            ).fetchall()
        return [dict(row) | {"evidence": json.loads(row["evidence_json"])} for row in rows]

    def set_issue_status(self, fingerprint: str, status: str) -> None:
        if status not in {"open", "muted", "fixed"}:
            raise ValueError(f"Unsupported improvement issue status: {status}")
        with self._conn() as conn:
            conn.execute(
                "UPDATE improvement_issues SET status=? WHERE fingerprint=?",
                (status, fingerprint),
            )

    def summary(self, repository: str) -> dict[str, int]:
        with self._conn() as conn:
            runs = conn.execute(
                "SELECT COUNT(*) FROM review_runs WHERE repository=?", (repository,)
            ).fetchone()[0]
            decisions = conn.execute(
                "SELECT decision, COUNT(*) AS count FROM finding_decisions fd "
                "JOIN review_runs rr ON rr.id=fd.run_id WHERE rr.repository=? "
                "GROUP BY decision",
                (repository,),
            ).fetchall()
            cases = conn.execute(
                "SELECT COUNT(*) FROM evaluation_cases ec JOIN review_runs rr "
                "ON rr.id=ec.run_id WHERE rr.repository=?",
                (repository,),
            ).fetchone()[0]
        return {
            "runs": int(runs),
            "evaluation_cases": int(cases),
            **{str(row["decision"]): int(row["count"]) for row in decisions},
        }
