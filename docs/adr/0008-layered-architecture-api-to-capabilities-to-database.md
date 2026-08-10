# 8. Layered architecture: api -> capabilities -> database

Date: 2026-08-10 | Status: Accepted

## Context
Routes reaching into the database and the database importing business logic made changes ripple unpredictably.

## Decision
import-linter enforces: api may import capabilities and database; capabilities may import database; nothing imports upward. The 21 pre-existing upward imports are a frozen baseline (ADR 0006).

## Consequences
Dependencies point one way, so layers can be tested and replaced independently. Background-executor access from capabilities must migrate to injected parameters.
