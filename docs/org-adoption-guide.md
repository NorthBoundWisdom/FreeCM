# Organization Adoption Guide

FreeCM is best treated as a multi-repository source workspace manager, not as a
general package manager, artifact registry, CI/CD orchestrator, or full supply
chain security platform.

## Pilot

Start with one small dependency chain. Keep `FreeCM/` as a submodule, expose
`configs/source_roots.py`, `configs/source_root_workflow.py`, and
`source_roots.lock.jsonc.in`, then validate:

```bash
python3 configs/source_root_workflow.py --init
python3 configs/source_root_workflow.py --update
python3 configs/source_roots.py status --format json
python3 configs/source_roots.py verify
```

## Ownership

Treat `source_roots.lock.jsonc.in` as the reviewed baseline. The active
`source_roots.lock.jsonc` is machine-local unless a host repository deliberately
tracks it.

Dependency code changes should happen in real manual checkouts selected through
`depsMode=manual` and `depsManualPath`, not in generated materialized roots.

## Upgrade Flow

Publish lower-level dependency commits first, confirm each SHA exists on its
remote with `git ls-remote <remote> <sha>`, then update parent lock templates in
dependency order.

## Policy Integration

Use JSON status, graph, audit, and policy reports for CI decisions:

```bash
python3 configs/source_roots.py policy-check --format json
python3 configs/source_roots.py graph --format json
python3 configs/source_roots.py audit --format json
python3 configs/source_roots.py explain-conflict GeometryCore --format json
```

`configs/freecm_policy.jsonc` can enforce approved remotes and mode constraints
such as `pinRequired`, `manualAllowed`, and `latestAllowed`. Large organizations
should layer their own approval, license, SBOM, and vulnerability systems around
FreeCM's lock and report data.

When two dependency paths declare the same logical dependency with incompatible
remote or commit values, `audit` and `explain-conflict` report the existing and
candidate declaration sources, their parent dependency names, the mismatched
field, and suggested remediation actions. This gives CI and dependency owners a
stable object to route instead of scraping a traceback or plain text error.

Use the optional catalog fields to connect FreeCM reports with internal
ownership and approval systems:

```jsonc
{
  "schemaVersion": 1,
  "allowedRemotes": ["https://github.com/my-org/*"],
  "dependencyCatalog": {
    "GeometryCore": {
      "owner": "Runtime Platform",
      "tier": "production",
      "license": "Apache-2.0",
      "approvalRequired": true
    }
  },
  "dependencyPolicies": {
    "GeometryCore": {
      "pinRequired": true,
      "manualAllowed": false,
      "latestAllowed": false,
      "abiGroup": "geometry-cpp-v2",
      "licenseAllowlist": ["Apache-2.0", "MIT"]
    }
  },
  "conflictPolicy": {
    "default": "fail",
    "allowDifferentAbiGroups": true,
    "allowSameDependencyDifferentCommit": false
  }
}
```

The FreeCM policy report preserves `dependencyCatalog` and emits stable
violation codes such as `abi-group-mismatch` and `license-not-allowed`. Treat
those as inputs to your own approval, license, vulnerability, and release gates
rather than as a replacement for those systems.
