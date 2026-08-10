"""Orivellum Runner — assign a task, walk away, read the report.

WHY THIS EXISTS
Needing to say "continue" is a harness problem, not a model problem. The loop
that keeps working is code, not conversation. So this package is a harness:

    task spec -> plan -> queue of LEAF UNITS -> one sub-agent per unit
              -> verification -> compaction -> checkpoint -> next unit
              -> final report

THREE STATE STORES, as the runtime literature prescribes:
  workspace   the target being analysed and the artifacts written (runs/<id>/)
  checkpoint  SQLite: which units ran, what came back, what runs next
              — this is what lets a killed run resume mid-flight
  artifacts   reports and digests, kept out of the checkpoint DB

CONTEXT DISCIPLINE
Each unit gets a sub-agent with a CLEAN context and returns a short digest, so
the parent's context grows by a paragraph per unit instead of by a file per
unit. Token budget is capped BELOW the model's real limit on purpose: an agent
that can see its own ceiling starts summarising prematurely and abandoning work.

TWO JOBS
  code   a program (zip or directory): functions, call graph, scanner findings,
         what is missing, hardening
  xlsx   a workbook: sheets, formula dependency graph, error cells, doctrine
         violations, and an invariant test suite that runs without Excel
"""
__version__ = "1.0.0"
