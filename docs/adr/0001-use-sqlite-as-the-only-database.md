# 1. Use SQLite as the only database

Date: 2026-08-10 | Status: Accepted

## Context
Orivellum runs entirely on one operator's Windows machine. A server database would add setup, services, and failure modes with no benefit at this scale.

## Decision
All persistence goes through a single SQLite file with WAL mode, guarded by one process-wide lock in the database layer. Migrations are an append-only numbered list in schema.py.

## Consequences
Zero-install persistence and trivial backups (online backup API only). The cost: one writer at a time, so long transactions must stay short and background work must hold the lock briefly.
