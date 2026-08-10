# 5. The code sandbox is an accident guard, not a security boundary

Date: 2026-08-10 | Status: Accepted

## Context
LLM-generated scripts (Workshop, Workbench) run on the operator's own machine. OS-level isolation (containers) is unavailable on the target Windows setup.

## Decision
Generated code runs with a scrubbed environment, no inherited network config, and POSIX resource caps where available. We document — rather than pretend otherwise — that a determined script could escape.

## Consequences
Honest threat model. Hardening (filesystem restriction, symlink rejection) is tracked as explicit follow-up work, not assumed.
