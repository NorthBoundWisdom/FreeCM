# Compatibility Policy

FreeCM is currently a `0.x` project.

During `0.x`:

- CLI flags should remain compatible when practical, but minor releases may
  adjust behavior with release notes.
- Lock schema changes must either migrate existing files or fail with a clear
  validation error.
- VS Code command IDs should remain stable.
- Machine-readable JSON outputs must include `schemaVersion`.
- Public FreeCM exception classes remain catch-compatible with their historical
  base classes where possible. For example, `LockfileValidationError` is still a
  `ValueError`, and seed/materialization errors remain `RuntimeError` variants.

After `1.0`:

- CLI breaking changes should only occur in major versions.
- Lock schema breaking changes must include a migration path.
- JSON report breaking changes must increment `schemaVersion`.
