# Status: Design docs & contracts for the native Forge capability

`orivellum-forge` holds the contracts, policies, templates, and evals that
define the Forge Website Factory. The **running implementation is native** in
the core product (`src/orivellum/capabilities/forge*`, plus the
`artifacts/forge-factory` UI); this directory is the design/contract source
material behind it.

- **Fate:** kept as active reference — JSON schemas here document the shapes
  the native capability implements.
- **Not imported** at runtime by `src/orivellum`; nothing breaks if this
  directory is removed, but the contracts remain useful when evolving Forge.
