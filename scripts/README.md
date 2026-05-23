# `scripts/` — lifecycle namespaces (WSH-F1 / WSH-F3)

Helper scripts are organized into four lifecycle namespaces so a stale one-shot
script does not silently rot next to load-bearing infrastructure, and so
`lint_script_lifecycle.py` can mechanically tell them apart.

| Namespace          | Lifecycle  | Lint        | What goes here |
|--------------------|------------|-------------|----------------|
| `scripts/infra/`   | persistent | **exempt**  | Long-lived infrastructure the system depends on every run. |
| `scripts/migrate/` | run-once   | opt-in      | Unidirectional schema/data migrations. |
| `scripts/seed/`    | one-shot   | **linted**  | Data-seeding / backfill scripts. |
| `scripts/tools/`   | ephemeral  | **linted**  | Ad-hoc / dev utilities, throwaway converters. |

## Guard — NEW scripts only

**Do not mass-relocate** existing top-level `scripts/*.py` / `*.sh` into these
subdirs: it breaks imports and hard-coded paths. The convention is for **new**
scripts going forward; relocating existing ones is a separate, out-of-scope
migration. Existing top-level scripts stay where they are and are **not** linted.

## Lifecycle markers (for scripts placed in `seed/` or `tools/`)

    # SAFE_DELETE_AFTER: YYYY-MM-DD   explicit retention date
    # RUN_ONCE                        intentionally one-shot

Run the lint: `python scripts/lint_script_lifecycle.py [--strict|--json|--verbose]`
