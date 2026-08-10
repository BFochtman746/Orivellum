# 10. The hand-written OpenAPI spec is the API contract

Date: 2026-08-10 | Status: Accepted

## Context
The UI's hooks and schemas are generated (Orval) from lib/api-spec/openapi.yaml. If the spec drifts from the live app, the frontend compiles against fiction.

## Decision
The spec is edited by hand, code is written to satisfy it, and CI (scripts/check_openapi_drift.py) fails when the spec references paths or methods the running app does not expose.

## Consequences
One reviewable contract file. The drift gate makes 'forgot to update the spec' and 'deleted an endpoint the UI still uses' loud instead of silent.
