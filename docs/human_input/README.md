# Human Input Checklist

> Tasks requiring human-provided files before autonomous execution can continue.
> Use `/collect-input` to check status, get guidance, validate, and unblock tasks.

<!-- CUSTOMIZE: Replace the example tasks below with your project's actual
     NEEDS-INPUT tasks. Each task should have its own section with status,
     description, target location, and validation command. -->

---

## T-XX-1: Example Input Task

- **Status**: [ ] Not started
- **What's needed**: Description of what files or configuration the human needs to provide
- **Target location**: `path/to/target/files/`
- **Template**: `path/to/template/file` (if applicable)
- **Validate**: `/collect-input validate T-XX-1`

---

## Protocol

1. Read the detail file for the task you want to unblock
2. Follow the instructions to create/place the required files
3. Run `/collect-input validate <task-id>` to check your work
4. On pass, run `/collect-input unblock <task-id>` to remove the `[NEEDS-INPUT]` tag
