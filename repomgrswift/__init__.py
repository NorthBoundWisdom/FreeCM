"""Swift/Xcode adapters for FreeCM dependency roots."""

__all__ = [
    "DependencyResolution",
    "ExtraDependencyPathSpec",
    "ResolvedSwiftDependencyRoots",
    "DependencyRootSpec",
    "DependencyRootWorkflow",
    "DependencyRootWorkflowConfig",
]


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from . import source_roots

    return getattr(source_roots, name)
