"""Swift/Xcode adapters for RepoConfigsMgr dependency roots."""

__all__ = [
    "DependencyResolution",
    "ExtraSourceRootPathSpec",
    "ResolvedSourceRoots",
    "SourceRootDependencySpec",
    "SourceRootWorkflow",
    "SourceRootWorkflowConfig",
    "SourceRootWorkflowScript",
    "SwiftConfigError",
    "load_swift_configs",
    "validate_swift_configs",
]


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    if name == "SourceRootWorkflowScript":
        from .source_root_workflow import SourceRootWorkflowScript

        return SourceRootWorkflowScript

    if name in {"SwiftConfigError", "load_swift_configs", "validate_swift_configs"}:
        from . import swift_configs

        return getattr(swift_configs, name)

    from . import source_roots

    return getattr(source_roots, name)
