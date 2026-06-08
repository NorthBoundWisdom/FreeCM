# Dependency Lock Schema

FreeCM lock files use JSONC and `schemaVersion: 5`.

The Python core and VS Code extension share a minimal lock-schema contract:
schema version, valid dependency modes, active/template lock filenames, field
names, and the workspace mutation lock name. Keep
`freecm.dependency_lock.LOCK_SCHEMA_CONTRACT` and
`vscode-extension/src/lockSchema.ts` aligned when changing these values.

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

`dependencyName` is the dependency map key. It is the logical name used by
manual-path overrides, environment maps, conflict diagnostics, and JSON reports.
`repoName` is the local seed/materialized repository directory name. Use
`repoName` only when the logical dependency name differs from the repository
checkout name.

Both dependency names and `repoName` values must be path-safe single segments.
The core accepts and removes the legacy dependency entry field `abiGroup` so
older locks can still be read, but new lock-mode writes do not preserve it.

## Workspace Mutation Lock

FreeCM uses `.freecm.workspace.lock` in the downstream repository root to
serialize workspace mutations across Python workflows and VS Code lock-mode
controls. The lock is a temporary directory, not a lock file; it is removed when
the operation finishes.

Operations that mutate seed repositories, materialized source roots, active lock
state, generated nested locks, or generated CMake presets should acquire this
workspace lock. Non-mutating diagnostics can remain lock-free. If a tool calls
another process that also acquires the workspace lock, release the outer lock
before spawning that process.

## Policy File

Hosts may add `configs/freecm_policy.jsonc`:

```jsonc
{
  "schemaVersion": 1,
  "allowedRemotes": ["https://github.com/my-org/*"],
  "remoteAliases": {
    "github.com/my-org/renamed-lib": "github.com/my-org/lib"
  },
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
      "licenseAllowlist": ["MIT", "Apache-2.0", "BSD-3-Clause"]
    }
  },
  "violationSeverities": {
    "remote-not-allowed": "warning"
  },
  "conflictPolicy": {
    "default": "fail"
  },
  "signaturePolicy": {
    "provider": "external-ci"
  },
  "refPolicy": {
    "allowedRefs": ["refs/heads/main", "refs/tags/v*"]
  },
  "sbomPolicy": {
    "reportPath": "build/reports/sbom.json"
  },
  "licensePolicy": {
    "reportPath": "build/reports/licenses.json"
  },
  "ownerApprovalPolicy": {
    "system": "internal-approval"
  },
  "vulnerabilityPolicy": {
    "reportPath": "build/reports/vulnerabilities.json"
  }
}
```

`allowedRemotes` uses shell-style glob patterns after FreeCM normalizes common
Git URL shapes. For example, `git@github.com:my-org/lib.git`,
`ssh://git@github.com/my-org/lib.git`, and `https://github.com/my-org/lib`
normalize to `github.com/my-org/lib`. `remoteAliases` maps normalized remotes to
canonical remotes before policy matching, which lets an organization treat
renamed mirrors or SSH/HTTPS aliases as equivalent.

`dependencyPolicies` is keyed by logical dependency name. `policy-check`
validates direct lock entries without requiring seed repositories. `audit`
applies the same policy to the resolved closure when local seed repositories are
available. JSON reports include `normalizedRemote` for every dependency so CI
can route both the original lock URL and the canonical comparison key.

`violationSeverities` may map policy violation codes to `warning` or `error`.
The default is `error`, preserving existing CI behavior. Warning violations are
still emitted in JSON reports with `"severity": "warning"`, but they do not make
`policy-check` or `audit` fail unless another error-severity violation or
conflict is present.

`audit` also reports dependency closure conflicts in `conflicts`. Each conflict
contains `dependencyName`, `fieldName`, `existing`, `candidate`, and
`suggestedActions`. Use `explain-conflict <dependency-name> --format json` to
extract the same diagnostic for one dependency without parsing human-readable
errors.

`resolve --format json` and `audit --format json` also include
`rootOverrideTransitivePinMismatches`. This warning list is populated when the
root lock directly declares a dependency commit that overrides a different
transitive pin from a nested lock template. Each warning contains
`dependencyName`, `rootCommit`, `transitiveCommit`, `rootSource`,
`transitiveSource`, and `parentDependencyName`. The warning is visible by
default but does not make `audit` fail unless policy or conflict checks also
fail.

`audit --format json` includes `modeWarnings` when dependencies resolve through
`manual` or `latest` mode. These warnings are informational: they make local
override and moving-resolution risk visible, but they do not change the current
`manual` or `latest` behavior and do not fail audit by themselves.

`dependencyCatalog` is optional organization metadata keyed by logical
dependency name. FreeCM preserves it in policy and audit JSON reports so CI can
join report rows with owner, tier, license, and approval data. When a policy
declares `licenseAllowlist` and the catalog entry has `license`, FreeCM reports
`license-not-allowed` if the catalog license is outside the allowlist.

`signaturePolicy`, `refPolicy`, `sbomPolicy`, `licensePolicy`,
`ownerApprovalPolicy`, and `vulnerabilityPolicy` are reserved organization
extension points. FreeCM validates that they are objects and preserves them in
`policyExtensions` JSON output, but it does not verify signatures, branch
membership, SBOM contents, owner approval, or vulnerability databases by itself.
Use these fields to connect FreeCM reports to the organization's dedicated
governance systems.
