# Dependency Lock Schema

FreeCM lock files use JSONC and `schemaVersion: 5`.

Required top-level fields:

- `schemaVersion`: currently `5`.
- `depsMode`: one of `pinned`, `latest`, or `manual`.
- `depsManualPath`: map from dependency name to manual checkout path or an empty
  string.
- `dependencies`: map from dependency name to dependency entry.

Optional top-level fields include `cmakeEnvironment`, `cmakeCacheVariables`,
`terminalPath`, and `assets`.

Dependency entry fields:

- `remote`: Git remote URL.
- `commit`: pinned commit SHA.
- `repoName`: optional repository checkout directory name. Defaults to the
  dependency name or the host config's `DependencyRootSpec.repo_name`.
- `latestRef`: optional ref used in `latest` mode.
- `abiGroup`: optional compatibility group label.

`dependencyName` is the dependency map key. It is the logical name used by
manual-path overrides, environment maps, conflict diagnostics, and JSON reports.
`repoName` is the local seed/materialized repository directory name. Use
`repoName` only when the logical dependency name differs from the repository
checkout name.

## Policy File

Hosts may add `configs/freecm_policy.jsonc`:

```jsonc
{
  "schemaVersion": 1,
  "allowedRemotes": ["https://github.com/my-org/*"],
  "dependencyCatalog": {
    "LibA": {
      "owner": "Core Platform",
      "tier": "production",
      "license": "MIT",
      "approvalRequired": true
    }
  },
  "dependencyPolicies": {
    "LibA": {
      "pinRequired": true,
      "manualAllowed": false,
      "latestAllowed": false,
      "abiGroup": "core-cpp-v1",
      "licenseAllowlist": ["MIT", "Apache-2.0", "BSD-3-Clause"]
    }
  },
  "conflictPolicy": {
    "default": "fail"
  }
}
```

`allowedRemotes` uses shell-style glob patterns. `dependencyPolicies` is keyed by
logical dependency name. `policy-check` validates direct lock entries without
requiring seed repositories. `audit` applies the same policy to the resolved
closure when local seed repositories are available.

`audit` also reports dependency closure conflicts in `conflicts`. Each conflict
contains `dependencyName`, `fieldName`, `existing`, `candidate`, and
`suggestedActions`. Use `explain-conflict <dependency-name> --format json` to
extract the same diagnostic for one dependency without parsing human-readable
errors.

`dependencyCatalog` is optional organization metadata keyed by logical
dependency name. FreeCM preserves it in policy and audit JSON reports so CI can
join report rows with owner, tier, license, and approval data. When a dependency
policy declares `abiGroup`, FreeCM reports `abi-group-mismatch` if the lock entry
uses a different `abiGroup`. When a policy declares `licenseAllowlist` and the
catalog entry has `license`, FreeCM reports `license-not-allowed` if the catalog
license is outside the allowlist.
