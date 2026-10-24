"""Offline configuration of existing shared seeds in a machine-local lock."""

from __future__ import annotations

from pathlib import Path

from .atomic_write import atomic_write_json
from .dependency_lock import ACTIVE_LOCK_FILE_NAME, load_dependency_lock_data
from .git_repositories import git_output, git_repository_state
from .seed_paths import public_seed_mutation_locks
from .workspace_lock import workspace_mutation_lock


def configure_public_seeds(repo_root: Path, seeds_root: Path) -> dict[str, Path]:
    """Select a checkout per enabled direct dependency without changing its pin."""
    repo_root = repo_root.resolve()
    seeds_root = seeds_root.expanduser().resolve()
    if not seeds_root.is_dir():
        raise FileNotFoundError(f"Public seed directory is missing: {seeds_root}")
    with workspace_mutation_lock(repo_root):
        active = repo_root / ACTIVE_LOCK_FILE_NAME
        data = load_dependency_lock_data(active)
        if data["depsMode"] != "pinned":
            raise ValueError("Configuring CI public seeds requires a pinned active lock")
        selected = {}
        for name, entry in data["dependencies"].items():
            if entry.get("disabled", False):
                continue
            seed = (seeds_root / name).resolve()
            if seed.parent != seeds_root:
                raise ValueError(f"Public seed escapes the selected directory: {seed}")
            entry["publicSeedPath"] = str(seed)
            selected[name] = seed
        # Check the whole selection before publishing any change to the lock.
        with public_seed_mutation_locks(data, repo_root):
            for name, seed in selected.items():
                state = git_repository_state(seed)
                if state is None or state.work_tree != seed:
                    raise ValueError(f"Public seed is not a checkout root: {seed}")
                if git_output(seed, "rev-parse", "--is-shallow-repository") != "false":
                    raise ValueError(f"Public seed must contain full history: {seed}")
                if (
                    git_output(seed, "config", "--get", "remote.origin.url")
                    != data["dependencies"][name]["remote"]
                ):
                    raise ValueError(f"Public seed remote mismatch for {name}: {seed}")
                if git_output(seed, "status", "--porcelain=v1", "--untracked-files=all"):
                    raise ValueError(f"Public seed is dirty: {seed}")
            atomic_write_json(active, data)
        return selected
