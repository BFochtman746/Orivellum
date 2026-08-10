"""Actions framework — typed, grounded, reversible actions.

Architecture
------------
Each action subclasses ActionBase and is registered with ACTION_REGISTRY.
The registry is populated on first import via _register_all().

Action lifecycle
----------------
1. Frontend / chat calls POST /api/actions/{name}/preview → confirmation message
2. User approves → POST /api/actions/{name}/execute
3. execute() writes an action_run row (status=running), calls _execute_impl(),
   updates status=done or error, writes an audit_log entry
4. Result includes a download URL served by the existing /studio/outputs/serve endpoint
"""
from __future__ import annotations

import json
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.actions")

# ── Registry ───────────────────────────────────────────────────────────────────

ACTION_REGISTRY: dict[str, ActionBase] = {}


def _register_all() -> None:
    """Import and register every action implementation."""
    from orivellum.capabilities.actions.book_export import BookExportAction
    from orivellum.capabilities.actions.report_assembler import ReportPackageAction
    from orivellum.capabilities.actions.study_plan import StudyPlanAction
    from orivellum.capabilities.actions.tax_package import TaxPackageAction
    from orivellum.capabilities.actions.template_fill import TemplateFillAction

    for cls in [
        TaxPackageAction,
        ReportPackageAction,
        BookExportAction,
        StudyPlanAction,
        TemplateFillAction,
    ]:
        inst = cls()
        ACTION_REGISTRY[inst.name] = inst


def get_registry() -> dict[str, ActionBase]:
    if not ACTION_REGISTRY:
        _register_all()
    return ACTION_REGISTRY


# ── DB helpers (raw SQL — avoids touching db.py) ───────────────────────────────

def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


def create_run(db: OrivellumDB, action_name: str, inputs: dict, work_id: str | None = None) -> str:
    """Insert a new action_runs row; return run_id."""
    run_id = _uid()
    with db._lock:
        db._conn.execute(
            """INSERT INTO action_runs
               (id, action_name, inputs, status, work_id, created_at)
               VALUES (?,?,?,?,?,?)""",
            (run_id, action_name, json.dumps(inputs), "running", work_id, _now()),
        )
        db._conn.commit()
    return run_id


def complete_run(
    db: OrivellumDB,
    run_id: str,
    output_path: str | None = None,
    output_label: str | None = None,
    output_doc_id: str | None = None,
) -> None:
    with db._lock:
        db._conn.execute(
            """UPDATE action_runs SET status='done', output_path=?, output_label=?,
               output_doc_id=?, completed_at=? WHERE id=?""",
            (output_path, output_label, output_doc_id, _now(), run_id),
        )
        db._conn.commit()


def fail_run(db: OrivellumDB, run_id: str, error: str) -> None:
    with db._lock:
        db._conn.execute(
            "UPDATE action_runs SET status='error', error=?, completed_at=? WHERE id=?",
            (error, _now(), run_id),
        )
        db._conn.commit()


def list_runs(db: OrivellumDB, limit: int = 30, work_id: str | None = None) -> list[dict]:
    with db._lock:
        if work_id:
            rows = db._conn.execute(
                "SELECT * FROM action_runs WHERE work_id=? ORDER BY created_at DESC LIMIT ?",
                (work_id, limit),
            ).fetchall()
        else:
            rows = db._conn.execute(
                "SELECT * FROM action_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_run(db: OrivellumDB, run_id: str) -> dict | None:
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM action_runs WHERE id=?", (run_id,)
        ).fetchone()
    return dict(row) if row else None


# ── Base class ─────────────────────────────────────────────────────────────────

class ActionBase(ABC):
    """Abstract base for all actions.

    Subclasses define ``name``, ``description``, ``input_schema``,
    implement ``confirm_message()`` and ``_execute_impl()``.
    ``execute()`` wraps the impl with run tracking and audit logging.
    """

    name: str
    description: str
    input_schema: dict  # JSON Schema dict describing expected inputs
    category: str = "general"

    def confirm_message(self, inputs: dict) -> str:
        """Human-readable summary of what the action will do.  Must be overridden."""
        return f"Run **{self.name}** with inputs: {json.dumps(inputs)}"

    @abstractmethod
    def _execute_impl(
        self,
        inputs: dict,
        db: OrivellumDB,
        cfg: OrivellumConfig,
    ) -> dict:
        """Carry out the action.

        Must return a dict with at least:
        - ``output_path``: str, relative to data_dir
        - ``output_label``: str, human-readable filename
        - ``output_doc_id``: str | None
        - ``summary``: str, one-sentence human-readable result
        """

    def execute(
        self,
        inputs: dict,
        db: OrivellumDB,
        cfg: OrivellumConfig,
    ) -> dict:
        """Public entry point.  Wraps impl with run tracking and audit logging."""
        run_id = create_run(db, self.name, inputs, work_id=inputs.get("work_id"))
        try:
            result = self._execute_impl(inputs, db, cfg)
            complete_run(
                db, run_id,
                output_path=result.get("output_path"),
                output_label=result.get("output_label"),
                output_doc_id=result.get("output_doc_id"),
            )
            # Write to audit_log using the existing helper
            try:
                db.add_audit_log(
                    actor="user",
                    operation=f"action.{self.name}",
                    object_id=result.get("output_doc_id"),
                    object_type="action_output",
                    result="ok",
                    detail=result.get("summary", self.name),
                )
            except Exception:
                pass
            return {"run_id": run_id, **result}
        except Exception as exc:
            fail_run(db, run_id, str(exc))
            logger.error("Action %s failed: %s", self.name, exc, exc_info=True)
            try:
                db.add_audit_log(
                    actor="user",
                    operation=f"action.{self.name}",
                    object_id=None,
                    object_type="action_output",
                    result="error",
                    detail=str(exc)[:200],
                )
            except Exception:
                pass
            raise

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "input_schema": self.input_schema,
        }
