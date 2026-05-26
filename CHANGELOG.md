# Changelog

All notable changes to FreeCM are documented here.

## [0.1.54] - Unreleased

### Fixed

- Fixed dependency alias handling when `dependencyName` differs from `repoName`
  for seed repositories, materialized roots, pinning, and nested dependency
  closure.

### Changed

- Added `VERSION` as the version source of truth and version consistency checks
  for Python and VS Code metadata.
- Expanded CI design for cross-platform Python and VS Code validation plus wheel
  and VSIX smoke tests.
- Added initial JSON graph, audit, and policy-check reports for organization
  integration.
- Consolidated repository documentation around README, release,
  security, schema, and organization adoption guides.

### Security

- Documented the security model for trusted workspaces, offline commands, safe
  dependency names, archives, and structured command manifests.
