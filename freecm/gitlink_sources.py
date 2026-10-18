"""Prepare pinned gitlink objects online; materialize them from local caches offline."""

from __future__ import annotations

import hashlib
import posixpath
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from .git_repositories import git, git_has_commit, git_output, remove_path
from .path_maps import resolve_dependency_relative_path


@dataclass(frozen=True)
class GitlinkSource:
    path: str
    commit: str
    remote: str
    cache: Path
    children: tuple[GitlinkSource, ...]


def _child_path(root: Path, path: str) -> Path:
    if root.is_symlink():
        raise ValueError(f"Gitlink root is a symlink: {root}")
    if any(part in {"", ".", "..", ".git"} for part in path.split("/")) or "\\" in path:
        raise ValueError(f"Unsafe gitlink path: {path!r}")
    target = resolve_dependency_relative_path(root, path, label="gitlink path")
    current = root
    for part in PurePosixPath(path).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Gitlink path contains a symlink: {current}")
    return target


def gitlink_cache_root(seed: Path) -> Path:
    metadata = seed / ".git"
    if metadata.is_symlink() or not metadata.is_dir():
        raise ValueError(f"Managed seed requires an independent Git directory: {seed}")
    return _child_path(metadata, "freecm-gitlinks")


def _independent_repository(repo: Path) -> None:
    metadata = repo / ".git"
    if metadata.is_symlink() or not metadata.is_dir():
        raise ValueError(f"Managed gitlink requires an independent Git directory: {repo}")
    if (metadata / "objects").is_symlink() or (metadata / "objects/info/alternates").exists():
        raise ValueError(f"Managed gitlink requires independent objects: {repo}")


def _remote(parent: str, url: str) -> str:
    if not url or url.startswith("-") or "\n" in url:
        raise ValueError(f"Invalid gitlink URL: {url!r}")
    if not url.startswith(("./", "../")):
        return url
    parsed = urlsplit(parent)
    if parsed.scheme and "://" in parent:
        return urlunsplit(parsed._replace(path=posixpath.normpath(parsed.path + "/" + url)))
    if ":" in parent and not Path(parent).is_absolute():
        host, path = parent.split(":", 1)
        return host + ":" + posixpath.normpath(path + "/" + url)
    return str((Path(parent) / url).resolve())


def _gitlinks(repo: Path, commit: str) -> dict[str, str]:
    links = {}
    for record in git_output(repo, "ls-tree", "-rz", commit).split("\0"):
        if not record:
            continue
        metadata, path = record.split("\t", 1)
        mode, _, sha = metadata.split()
        if mode == "160000":
            _child_path(repo, path)
            links[path] = sha
    return links


def _clean(repo: Path) -> None:
    _independent_repository(repo)
    # Ignore unstaged gitlink HEAD differences, but preserve index edits as well.
    if git_output(repo, "diff", "--cached", "--name-only", "--ignore-submodules=none"):
        raise ValueError(f"Refusing to overwrite modified gitlink index: {repo}")
    if git_output(
        repo, "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=all"
    ):
        raise ValueError(f"Refusing to overwrite modified gitlink source: {repo}")
    for path in _gitlinks(repo, "HEAD"):
        child = _child_path(repo, path)
        if (child / ".git").exists():
            _clean(child)
        elif child.exists() and any(child.iterdir()):
            raise ValueError(f"Nonempty uninitialized gitlink path: {child}")


def gitlink_sources(
    repo: Path,
    commit: str,
    remote: str,
    cache_root: Path,
    *,
    allow_network: bool = False,
    _ancestors: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[GitlinkSource, ...]:
    links = _gitlinks(repo, commit)
    if not links:
        return ()
    identity = (remote, commit)
    if identity in _ancestors:
        raise ValueError(f"Recursive gitlink cycle: {remote}@{commit}")
    ancestors = _ancestors | {identity}
    configured = git_output(repo, "config", "--null", "--blob", f"{commit}:.gitmodules", "--list")
    values = dict(record.split("\n", 1) for record in configured.split("\0") if record)
    urls = {}
    for key, path in values.items():
        if key.startswith("submodule.") and key.endswith(".path"):
            urls[path] = _remote(remote, values[key[:-5] + ".url"])

    def prepare_link(item: tuple[str, str]) -> GitlinkSource:
        path, sha = item
        if path not in urls:
            raise ValueError(f"Missing .gitmodules URL for {path} at {commit}")
        url = urls[path]
        key = hashlib.sha256((url + "\0" + sha).encode()).hexdigest()
        cache = _child_path(cache_root, key)
        if not (cache / ".git").exists():
            if not allow_network:
                raise FileNotFoundError(f"Missing gitlink seed for {path}@{sha}; run --init")
            if cache.exists() and any(cache.iterdir()):
                raise ValueError(f"Nonempty gitlink cache: {cache}")
            cache.mkdir(parents=True, exist_ok=True)
            git(cache, "init", quiet=True)
            git(cache, "remote", "add", "origin", url)
        _independent_repository(cache)
        if git_output(cache, "remote", "get-url", "origin") != url:
            raise ValueError(f"Gitlink cache remote mismatch: {cache}")
        if not git_has_commit(cache, sha):
            if not allow_network:
                raise FileNotFoundError(f"Missing gitlink commit {sha}; run --init")
            git(cache, "fetch", "--depth", "1", "origin", sha, quiet=True)
        # Dedicated immutable caches hold one commit, never a tracking branch.
        head = git(cache, "rev-parse", "--verify", "HEAD", check=False, capture_output=True)
        if head.returncode == 0:
            _clean(cache)
            if head.stdout.strip() != sha:
                raise ValueError(f"Gitlink cache HEAD mismatch: {cache}")
        elif allow_network:
            git(cache, "checkout", "--detach", sha, quiet=True)
        else:
            raise ValueError(f"Uninitialized gitlink cache: {cache}; run --init")
        children = gitlink_sources(
            cache, sha, url, cache_root, allow_network=allow_network, _ancestors=ancestors
        )
        return GitlinkSource(path, sha, url, cache, children)

    return tuple(prepare_link(item) for item in links.items())


def preflight_gitlinks(root: Path) -> None:
    if not (root / ".git").exists():
        return
    for path in _gitlinks(root, "HEAD"):
        child = _child_path(root, path)
        if (child / ".git").exists():
            _clean(child)
        elif child.exists() and any(child.iterdir()):
            raise ValueError(f"Nonempty uninitialized gitlink path: {child}")


def remove_obsolete_gitlinks(root: Path, sources: tuple[GitlinkSource, ...]) -> None:
    if not (root / ".git").exists():
        return
    desired = {source.path for source in sources}
    for path in _gitlinks(root, "HEAD").keys() - desired:
        child = _child_path(root, path)
        if (child / ".git").exists():
            _clean(child)
        elif child.exists() and any(child.iterdir()):
            raise ValueError(f"Nonempty uninitialized gitlink path: {child}")
        remove_path(child)


def materialize_gitlinks(root: Path, sources: tuple[GitlinkSource, ...]) -> None:
    for source in sources:
        target = _child_path(root, source.path)
        if not (target / ".git").exists():
            if target.exists() and any(target.iterdir()):
                raise ValueError(f"Nonempty gitlink target: {target}")
            target.mkdir(parents=True, exist_ok=True)
            git(target, "init", quiet=True)
            git(target, "remote", "add", "origin", source.remote)
        else:
            _clean(target)
            remove_obsolete_gitlinks(target, source.children)
        if not git_has_commit(target, source.commit):
            # Explicit local transport only; no remote URL is consulted offline.
            git(
                target,
                "-c",
                "protocol.allow=never",
                "-c",
                "protocol.file.allow=always",
                "fetch",
                "--no-tags",
                str(source.cache),
                source.commit,
                quiet=True,
            )
        git(target, "checkout", "--detach", source.commit, quiet=True)
        materialize_gitlinks(target, source.children)
        if git_output(target, "status", "--porcelain", "--untracked-files=all"):
            raise ValueError(f"Gitlink materialization is not clean: {target}")


def verify_gitlinks(root: Path, commit: str) -> list[str]:
    problems = []
    for path, sha in _gitlinks(root, commit).items():
        child = _child_path(root, path)
        if not (child / ".git").exists():
            problems.append(f"Uninitialized gitlink: {child}; run --init then --update")
            continue
        actual = git_output(child, "rev-parse", "HEAD")
        if actual != sha:
            problems.append(f"Gitlink commit mismatch: {child}: expected {sha}, got {actual}")
        else:
            problems.extend(verify_gitlinks(child, sha))
    return problems
