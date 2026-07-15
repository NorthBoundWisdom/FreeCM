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

Choose and document a host-level FreeCM gitlink policy. For owner-managed
repositories that follow `FreeCM/master`, set `submodule.FreeCM.branch` to
`master` in `.gitmodules` and run this only from a clean host primary branch:

```bash
git submodule update --remote --checkout FreeCM
```

Treat an unchanged gitlink as a silent no-op. When the gitlink changes, run the
compatibility and host validation below, commit on the existing primary branch,
and push that branch directly. Do not create an update branch or pull request
for this owner-managed mode. If validation fails, do not publish the new
gitlink. Repositories that require review can choose a different publication
policy explicitly; FreeCM does not choose one for them.

Before a repository adopts a newer FreeCM revision, run a read-only lock
compatibility check against the reviewed template and any deliberately tracked
active lock:

```bash
python3 -m repomgrcpp.tools.repo_tool check-lock-compat --repo-root .
python3 -m repomgrcpp.tools.repo_tool check-lock-compat --format json --repo-root .
```

The command reports unsupported schema versions, removed fields, stale
dependency-entry fields, and validation failures without mutating either lock
file.

## Policy Integration

Use JSON status, graph, audit, and policy reports for CI decisions:

```bash
python3 configs/source_roots.py policy-check --format json
python3 configs/source_roots.py graph --format json
python3 configs/source_roots.py audit --format json
python3 configs/source_roots.py explain-conflict LibCore --format json
```

`configs/freecm_policy.jsonc` can enforce approved remotes and mode constraints
such as `pinRequired`, `manualAllowed`, and `latestAllowed`. FreeCM normalizes
common Git URL shapes before matching `allowedRemotes`, so SSH and HTTPS forms
of the same GitHub repository can be treated as one remote. Use `remoteAliases`
when a renamed repository or mirror should be routed to the same canonical
policy identity.

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
    "LibCore": {
      "owner": "Runtime Platform",
      "tier": "production",
      "license": "Apache-2.0",
      "approvalRequired": true
    }
  },
  "dependencyPolicies": {
    "LibCore": {
      "pinRequired": true,
      "manualAllowed": false,
      "latestAllowed": false,
      "licenseAllowlist": ["Apache-2.0", "MIT"]
    }
  },
  "violationSeverities": {
    "remote-not-allowed": "warning"
  },
  "conflictPolicy": {
    "default": "fail"
  }
}
```

Use `violationSeverities` only when rollout needs a measured warning period.
Default severity is `error`, so existing CI gates remain strict unless a policy
explicitly downgrades a violation code.

The FreeCM policy report preserves `dependencyCatalog` and emits stable
violation codes such as `license-not-allowed`. It also preserves extension-point
objects such as `signaturePolicy`, `refPolicy`, `sbomPolicy`,
`ownerApprovalPolicy`, and `vulnerabilityPolicy` in `policyExtensions`.

Treat FreeCM policy as a governance-ready foundation object, not as a complete
supply-chain security platform. It gives CI stable dependency, conflict, owner,
license, and remote-normalization data; organizations should connect that data
to their own signature verification, allowed-ref enforcement, SBOM/license
scanning, owner approval, vulnerability, and release gates.

## Downstream Feedback Loop

During rollout, collect feedback from each downstream repository in the same
shape so recurring issues can become reusable FreeCM behavior instead of
project-local workarounds:

- Repository and adapter type: C++/CMake, Swift/Xcode, Android, .NET, or mixed.
- Workflow phase: init, update, materialize, verify, build, test, package, or
  VS Code lock controls.
- Current lock mode and whether the active lock is tracked or machine-local.
- Exact command, exit code, and the first FreeCM error message.
- Whether the issue reproduces after `--init` followed by offline `--update`.
- Any downstream-specific wiring that should remain outside FreeCM core.

Fold durable lessons into the owning document: lock schema details belong in
`docs/dependency-lock-schema.md`, rollout policy belongs here, hook behavior
belongs in `hooks/README.md`, and agent wiring steps belong in
`.codex/freecm-wiring/SKILL.md`.

## Performance Baselines

Use the lightweight benchmark command for local trend checks when dependency
graphs or lock parsing behavior changes:

```bash
python3 -m repomgrcpp.tools.repo_tool performance-baseline --dependencies 50 --iterations 25
```

The command prints JSON timings for JSONC parsing, lock validation, dependency
closure resolution, and dependency path-map generation. Treat the numbers as a
local baseline rather than a hard CI gate unless a repository has stable enough
runners to support performance thresholds.
