# Changelog

All notable changes to FreeCM are documented here.

## [0.1.71] - 2026-06-08

### Fixed

- Fixed dependency alias handling when `dependencyName` differs from `repoName`
  for seed repositories, materialized roots, pinning, and nested dependency
  closure.
- Fixed VS Code `Pin latest` so it does not hold the workspace mutation lock
  while spawning the offline Python `--update` workflow.
- Fixed target-scoped CMake compiler flags so
  `MSVC_EMBEDDED_DEBUG_INFO` applies the target
  `MSVC_DEBUG_INFORMATION_FORMAT` property.

### Changed

- Added `VERSION` as the version source of truth and version consistency checks
  for Python and VS Code metadata.
- Expanded CI design for cross-platform Python and VS Code validation plus wheel
  and VSIX smoke tests.
- Added initial JSON graph, audit, and policy-check reports for organization
  integration.
- Consolidated repository documentation around README, release,
  security, schema, and organization adoption guides.
- Added a shared workspace mutation lock used by Python workflows and VS Code
  lock-mode controls.
- Consolidated nested dependency active-lock generation and remove-path helpers
  into reusable core helpers.
- Added target-oriented CMake compiler flag APIs while preserving the existing
  global API.
- Added policy violation severity downgrades and informational audit warnings
  for `manual` and `latest` dependency modes.
- Aligned VS Code lock-mode schema validation with the Python core contract.

### Security

- Documented the security model for trusted workspaces, offline commands, safe
  dependency names, archives, and structured command manifests.
- Added regression tests proving non-`--init` dependency paths stay offline and
  do not clone, fetch, or download dependency state.
