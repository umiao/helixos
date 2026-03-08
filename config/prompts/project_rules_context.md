## Project Rules (from CLAUDE.md)

### Task Planning Rules
1. Scenario matrix: list ALL condition branches with expected outcomes.
2. Journey-first ACs: at least one AC per task must be a full user journey.
3. Cross-boundary integration: when spanning backend + frontend, at least one
   AC must verify end-to-end wiring.
4. "Other case" gate: every conditional AC must specify what happens when false.
5. Manual smoke test AC: every UX task needs a manual verification AC.
6. New-field consumer audit: when adding a model field, list all components
   that render related data and verify each uses the correct source of truth.

### Key Constraints
- All API keys and cookies from .env, never hardcoded.
- Every function must have type hints and docstring.
- No emoji characters anywhere in the project.
- Explicit UTF-8 for all file I/O and subprocess calls.
- Windows-compatible: no bash-only commands without PowerShell alternatives.
- Schema changes require migration (never assume users will delete their database).
