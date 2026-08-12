"""FreeCM core dependency-root workflow helpers."""

__all__ = [
    "__version__",
    "DEPENDENCY_LOCK_SCHEMA_VERSION",
    "DependencyDeclaration",
    "DependencyPin",
    "DependencyRootSummary",
    "DependencyClosure",
    "DependencyRootManager",
    "DependencyRootSpec",
    "DependencyRootConfig",
    "AppConfigError",
    "AppConfigValue",
    "FreeCMError",
    "LockfileValidationError",
    "MaterializationError",
    "RepoCommandManifestError",
    "RepoCommandManifestSummary",
    "ResolvedDependencyRoots",
    "SeedRepositoryError",
    "bind_dependency_root_workflow",
    "load_app_configs",
    "prepare_asset_seeds",
    "require_asset_seeds",
    "validate_repo_command_manifest",
    "validate_repo_command_manifest_text",
    "validate_app_configs",
]


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    if name == "__version__":
        from ._version import __version__

        return __version__

    if name in {"prepare_asset_seeds", "require_asset_seeds"}:
        from . import asset_seeds

        return getattr(asset_seeds, name)

    if name in {"AppConfigError", "AppConfigValue", "load_app_configs", "validate_app_configs"}:
        from . import app_configs

        return getattr(app_configs, name)

    if name in {
        "FreeCMError",
        "LockfileValidationError",
        "MaterializationError",
        "RepoCommandManifestError",
        "SeedRepositoryError",
    }:
        from . import errors

        return getattr(errors, name)

    if name in {
        "RepoCommandManifestSummary",
        "validate_repo_command_manifest",
        "validate_repo_command_manifest_text",
    }:
        from . import repo_commands

        return getattr(repo_commands, name)

    from . import dependency_roots

    return getattr(dependency_roots, name)
