# Security Policy

## Reporting

Report security issues privately through GitHub Security Advisories for this
repository. If advisories are unavailable in your mirror, contact the repository
maintainer and avoid filing a public issue with exploit details.

## Supported Versions

FreeCM is pre-1.0. Security fixes are made on `master` and shipped in the next
tagged release.

## Security Boundaries

FreeCM expects trusted repositories and reviewed lock files. Do not run FreeCM
commands against an untrusted workspace without reviewing:

- `source_roots.lock.jsonc` and `source_roots.lock.jsonc.in`;
- `configs/source_root_workflow.py`;
- `configs/freecm.commands.jsonc`;
- hook configuration under `hooks/`.

`--init` is the only dependency workflow allowed to use the network. Offline
commands such as `--update`, `materialize`, `verify`, `status`, VS Code lock-mode
controls, and command validation must not clone, fetch, download, or prepare
remote assets.

Dependency names and repository names must be single safe path segments. Archive
extraction must reject absolute paths, parent traversal, and symlink escapes.
VS Code project commands must use structured `command` plus `args` or `steps`;
FreeCM does not accept arbitrary shell strings in command manifests.
