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
    "FreeCMError",
    "LockfileValidationError",
    "MaterializationError",
    "ResolvedDependencyRoots",
    "SeedRepositoryError",
    "bind_dependency_root_workflow",
    "prepare_asset_seeds",
    "require_asset_seeds",
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

    if name in {
        "FreeCMError",
        "LockfileValidationError",
        "MaterializationError",
        "SeedRepositoryError",
    }:
        from . import errors

        return getattr(errors, name)

    from . import dependency_roots

    return getattr(dependency_roots, name)
