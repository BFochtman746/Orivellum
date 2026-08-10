"""Report/package assembler action.

Compiles a Work's knowledge, documents, and tasks into a downloadable
.docx report (and optionally a .pdf) using the existing generate.py helpers.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from orivellum.capabilities.actions import ActionBase

if TYPE_CHECKING:
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.actions.report_assembler")


class ReportPackageAction(ActionBase):
    name = "report_assembler"
    description = (
        "Compile a Work's knowledge items, document list, and open tasks into a "
        "formatted .docx research report, ready to download or share."
    )
    category = "export"
    input_schema = {
        "type": "object",
        "properties": {
            "work_id": {"type": "string", "description": "The Work to compile"},
            "format": {
                "type": "string",
                "enum": ["docx", "pdf"],
                "description": "Output format (default: docx)",
            },
        },
        "required": ["work_id"],
    }

    def confirm_message(self, inputs: dict) -> str:
        fmt = inputs.get("format", "docx").upper()
        return (
            f"Compile this Work's knowledge base, source document list, and open tasks "
            f"into a formatted **{fmt}** research report you can download."
        )

    def _execute_impl(self, inputs: dict, db: OrivellumDB, cfg: OrivellumConfig) -> dict:
        work_id: str = inputs["work_id"]
        fmt: str = inputs.get("format", "docx").lower()

        work = db.get_work(work_id)
        if not work:
            raise ValueError(f"Work {work_id!r} not found")

        if fmt == "pdf":
            from orivellum.capabilities.generate import generate_pdf_report

            fpath, doc_id = generate_pdf_report(work_id, db, cfg)
        else:
            from orivellum.capabilities.generate import generate_docx_report

            fpath, doc_id = generate_docx_report(work_id, db, cfg)

        from pathlib import Path

        data_dir = Path(cfg.data_dir)
        rel_path = str(fpath.relative_to(data_dir))
        label = fpath.name
        title = work.get("title", "Work")

        return {
            "output_path": rel_path,
            "output_label": label,
            "output_doc_id": doc_id,
            "summary": f"Report for '{title}' assembled as {fmt.upper()} ({fpath.stat().st_size // 1024} KB)",
        }
