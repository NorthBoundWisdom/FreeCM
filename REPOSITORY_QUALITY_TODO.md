# FreeCM Repository Quality TODO

Last updated: 2026-06-11

This file only tracks unfinished repository-quality work. Completed items from
the original quality review have been folded into `pyproject.toml`, CI,
validation docs, and tests.

## High Priority

### Tighten Python Type Checking

Current state:
- `mypy` is configured and runs in CI.
- The initial gate uses explicit adoption-period exemptions for existing type
  debt.

Remaining work:
- Remove `disable_error_code` entries from `[tool.mypy]` incrementally.
- Replace mixin-heavy `Any` flows with protocols or typed base interfaces where
  useful.
- Tighten direct-script import fallback typing without breaking user-callable
  entry points.
- Keep CI green after each stricter rule is enabled.

## Medium Priority

### Expand Ruff / Black Enforcement

Current state:
- `ruff` and `black` are configured.
- CI currently runs a narrow `ruff` gate for syntax and undefined-name classes.

Remaining work:
- Normalize existing Python formatting before enabling `black --check` in CI.
- Expand `ruff` rules toward `E`, `W`, `F`, `I`, `N`, `UP`, and `B`.
- Decide whether import sorting should be owned by Ruff `I` rules or a separate
  `isort` invocation.
- Fix existing long lines, import-order issues, and unused imports before
  tightening the gate.

### Add Coverage Thresholds

Current state:
- CI runs `coverage run` and `coverage report`.
- No fail-under threshold is enforced yet.

Remaining work:
- Choose an initial realistic coverage threshold from the current source-only
  baseline.
- Add `coverage report --fail-under=<threshold>` once the threshold is agreed.
- Consider uploading coverage HTML or XML as a CI artifact.
- Revisit thresholds after package/deployment helpers gain more targeted tests.

### Reduce Bandit Skips

Current state:
- CI runs `bandit` with explicit skips for known CLI/tooling false positives and
  accepted legacy findings.

Remaining work:
- Review each skipped rule in `[tool.bandit]`.
- Replace weak or noisy patterns where practical, for example non-security MD5
  use, executable permissions, URL handling, XML parsing, and subprocess helper
  patterns.
- Remove skip codes as findings are fixed or documented with targeted
  `# nosec` annotations.

### Improve Error Messages And Diagnostics

Current state:
- `FREECM_DEBUG` exists.
- Error classes are structured.

Remaining work:
- Add recovery hints for common lock, materialization, seed repository, and
  command-manifest failures.
- Consider consistent `--verbose` and `--quiet` behavior across user-facing
  CLIs.
- Add documentation links to high-friction error messages where stable docs
  exist.

### Add Lock Schema Compatibility Tools

Current state:
- Lock files use `schemaVersion`.
- Breaking behavior is documented through normal project docs and changelog
  practices.

Remaining work:
- Add migration helpers for future lock schema changes.
- Add a compatibility-check command that reports unsupported or stale lock
  fields without mutating files.
- Keep `CHANGELOG.md` breaking-change notes aligned with schema migrations.

### Strengthen Test Isolation Further

Current state:
- Full `unittest` discovery passes under coverage.
- Tests use temporary directories and git fixture helpers.

Remaining work:
- Audit tests for hidden cwd, environment, or global-state coupling.
- Check whether important subsets can run safely in parallel.
- Add helper cleanup assertions where repeated git fixture creation could leak
  state.

## Lower Priority

### Add Performance Benchmarks

Remaining work:
- Add benchmarks for dependency closure resolution, seed preparation, and
  source-root materialization.
- Prefer lightweight timing tests or a dedicated benchmark command that does not
  make regular CI flaky.
- Track baseline numbers for representative dependency graphs.

### Collect Downstream Adoption Feedback

Remaining work:
- Gather recurring FreeCM usage issues from downstream repositories.
- Identify reusable wiring templates and validation patterns.
- Fold durable lessons into `README.md`, `docs/org-adoption-guide.md`, or
  `.codex/freecm-wiring/SKILL.md` instead of leaving planning notes here.
