"""Shared dependency-root workflow helpers."""

__all__ = [
    "DEPENDENCY_LOCK_SCHEMA_VERSION",
    "DependencyDeclaration",
    "DependencyPin",
    "DependencyRootSummary",
    "DependencyClosure",
    "DependencyRootManager",
    "DependencyRootSpec",
    "DependencyRootConfig",
    "ResolvedDependencyRoots",
    "bind_dependency_root_workflow",
]


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from . import dependency_roots

    return getattr(dependency_roots, name)
