# 3. Single-operator security model with session-cookie auth

Date: 2026-08-10 | Status: Accepted

## Context
Exactly one person uses this system, on their own hardware, on their own network (plus Tailscale). Multi-tenant machinery would be dead weight.

## Decision
Web auth is a session cookie behind require_auth on every router. Secrets live in the auth_keys path only. There are no roles, orgs, or per-user rows.

## Consequences
Simple and auditable. If the system is ever exposed to a second user, this ADR must be superseded first.
