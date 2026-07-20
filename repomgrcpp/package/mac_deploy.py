from __future__ import annotations

import fnmatch
import shutil
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .common import (
    PackageConfig,
    PackageError,
    clean_dir,
    clean_dist_dir,
    copy_configured_resources,
    copy_file,
    ensure_dir,
    log,
    run_command,
    warn,
)

SYSTEM_LIBRARY_PREFIXES = ("/System/Library/", "/usr/lib/")
OTOOL_BATCH_SIZE = 64
MAC_DEPLOYMENT_TOOLS = frozenset({"native", "qt"})


def _iter_tree_files(root: Path, *, include_symlinks: bool = False) -> Iterator[Path]:
    for path in root.rglob("*"):
        if path.is_file() and (include_symlinks or not path.is_symlink()):
            yield path


@dataclass(frozen=True)
class LibrarySearchIndex:
    roots: tuple[Path, ...]
    files: tuple[Path, ...]
    by_name: dict[str, Path]
    relative_by_path: dict[Path, str]

    def find(self, name: str) -> Path | None:
        return self.by_name.get(name)

    def matching(self, pattern: str) -> tuple[Path, ...]:
        matches: list[Path] = []
        normalized_pattern = pattern.replace("\\", "/")
        for path in self.files:
            relative = self.relative_by_path[path]
            if "/" not in normalized_pattern:
                matched = "/" not in relative and fnmatch.fnmatchcase(relative, normalized_pattern)
            else:
                matched = Path(relative).match(normalized_pattern) or (
                    normalized_pattern.startswith("**/")
                    and Path(relative).match(normalized_pattern.removeprefix("**/"))
                )
            if matched:
                matches.append(path)
        return tuple(matches)


@dataclass(frozen=True)
class RpathChanges:
    delete_args: tuple[str, ...]
    add_args: tuple[str, ...]


def build_library_search_index(search_paths: Iterable[Path]) -> LibrarySearchIndex:
    roots = tuple(path for path in search_paths if path.is_dir())
    ranked_files: list[tuple[int, int, str, Path]] = []
    for root_index, root in enumerate(roots):
        for path in _iter_tree_files(root, include_symlinks=True):
            relative = path.relative_to(root)
            ranked_files.append(
                (root_index, 0 if path.parent == root else 1, relative.as_posix(), path)
            )
    files_list: list[Path] = []
    by_name: dict[str, Path] = {}
    relative_by_path: dict[Path, str] = {}
    for _root_index, _priority, relative_text, indexed_path in sorted(ranked_files):
        if indexed_path in relative_by_path:
            continue
        files_list.append(indexed_path)
        relative_by_path[indexed_path] = relative_text
        by_name.setdefault(indexed_path.name, indexed_path)
    files = tuple(files_list)
    return LibrarySearchIndex(
        roots=roots,
        files=files,
        by_name=by_name,
        relative_by_path=relative_by_path,
    )


def parse_otool_deps(output: str) -> list[str]:
    deps: list[str] = []
    for line in output.splitlines()[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        deps.append(stripped.split(" (compatibility version")[0].strip())
    return deps


def find_library(
    name: str,
    search_paths: list[Path],
    *,
    index: LibrarySearchIndex | None = None,
) -> Path | None:
    return (index or build_library_search_index(search_paths)).find(name)


def _macho_magic(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            return stream.read(4)
    except OSError:
        return b""


def is_macho_file(path: Path) -> bool:
    return _macho_magic(path) in {
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xd0\x0d",
        b"\x0d\xd0\xfe\xca",
    }


def parse_otool_rpaths(output: str) -> list[str]:
    rpaths: list[str] = []
    in_rpath = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped == "cmd LC_RPATH":
            in_rpath = True
            continue
        if in_rpath and stripped.startswith("path "):
            rpaths.append(stripped.split(" (offset", 1)[0].removeprefix("path ").strip())
            in_rpath = False
    return list(dict.fromkeys(rpaths))


def build_sign_command(
    path: Path, *, identity: str = "-", entitlements: Path | None = None, runtime: bool = False
) -> list[str]:
    command = ["codesign", "--force", "--sign", identity]
    if entitlements is not None:
        command.extend(["--entitlements", str(entitlements)])
    # Library validation is meaningful only for an identity with a Team ID.
    # Applying the hardened runtime to an ad-hoc bundle makes macOS reject its
    # independently signed private dylibs as belonging to a different team.
    if runtime and identity != "-":
        command.extend(["--options", "runtime"])
    if identity != "-":
        command.append("--timestamp")
    command.append(str(path))
    return command


def collect_bundle_binaries(bundle: Path) -> list[Path]:
    binaries: list[Path] = []
    contents = bundle / "Contents"
    macos_dir = contents / "MacOS"
    for path in _iter_tree_files(contents):
        if path.suffix in {".dylib", ".so", ".bundle"} or path.is_relative_to(macos_dir):
            binaries.append(path)
            continue
        framework_part = next((part for part in path.parts if part.endswith(".framework")), None)
        if framework_part:
            framework_name = Path(framework_part).stem
            if path.name == framework_name and is_macho_file(path):
                binaries.append(path)
    return sorted(set(binaries))


def split_otool_output(output: str, binaries: Iterable[Path]) -> dict[Path, str]:
    binary_list = tuple(binaries)
    if len(binary_list) == 1:
        return {binary_list[0]: output}
    markers = {f"{binary}:": binary for binary in binary_list}
    chunks: dict[Path, list[str]] = {}
    current: Path | None = None
    for line in output.splitlines():
        marker = markers.get(line.rstrip())
        if marker is not None:
            current = marker
            chunks[current] = [line]
        elif current is not None:
            chunks[current].append(line)
    return {path: "\n".join(lines) for path, lines in chunks.items()}


def inspect_otool_outputs(
    binaries: Iterable[Path],
    mode: str,
    *,
    prefix: str,
    allow_failures: bool,
    batch_size: int = OTOOL_BATCH_SIZE,
) -> dict[Path, str | None]:
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")
    binary_list = tuple(dict.fromkeys(binaries))
    outputs: dict[Path, str | None] = {}
    for start in range(0, len(binary_list), batch_size):
        batch = binary_list[start : start + batch_size]
        completed = run_command(
            ["otool", mode, *(str(binary) for binary in batch)],
            check=False,
            capture=True,
            prefix=prefix,
        )
        parsed = (
            split_otool_output(completed.stdout or "", batch) if completed.returncode == 0 else {}
        )
        if len(batch) == 1 and completed.returncode != 0:
            binary = batch[0]
            if allow_failures:
                outputs[binary] = None
                continue
            detail = (completed.stderr or completed.stdout or "").strip()
            raise PackageError(
                f"otool {mode} failed for {binary}" + (f": {detail}" if detail else "")
            )
        for binary in batch:
            if binary in parsed:
                outputs[binary] = parsed[binary]
                continue
            fallback = run_command(
                ["otool", mode, str(binary)],
                check=False,
                capture=True,
                prefix=prefix,
            )
            if fallback.returncode == 0:
                outputs[binary] = fallback.stdout or ""
            elif allow_failures:
                outputs[binary] = None
            else:
                detail = (fallback.stderr or fallback.stdout or "").strip()
                raise PackageError(
                    f"otool {mode} failed for {binary}" + (f": {detail}" if detail else "")
                )
    return outputs


def _run_install_name_tool(args: list[str], *, prefix: str) -> None:
    run_command(["install_name_tool", *args], capture=True, prefix=prefix)


def _bundle_rpath_changes(binaries: Iterable[Path], *, prefix: str) -> dict[Path, RpathChanges]:
    bundle_framework_rpath = "@executable_path/../Frameworks"
    absolute_prefixes = ("/opt/homebrew/", "/usr/local/")
    changes: dict[Path, RpathChanges] = {}
    outputs = inspect_otool_outputs(
        binaries,
        "-l",
        prefix=prefix,
        allow_failures=False,
    )
    for binary, output in outputs.items():
        rpaths = parse_otool_rpaths(output or "")
        if not rpaths:
            continue
        delete_args: list[str] = []
        for rpath in rpaths:
            delete_args.extend(["-delete_rpath", rpath])
        ordered = [bundle_framework_rpath]
        ordered.extend(
            rpath
            for rpath in rpaths
            if rpath != bundle_framework_rpath and not rpath.startswith(absolute_prefixes)
        )
        add_args: list[str] = []
        for rpath in dict.fromkeys(ordered):
            add_args.extend(["-add_rpath", rpath])
        changes[binary] = RpathChanges(tuple(delete_args), tuple(add_args))
    return changes


def normalize_bundle_rpaths(
    bundle: Path,
    *,
    prefix: str,
    binaries: Iterable[Path] | None = None,
) -> None:
    binary_list = tuple(binaries or collect_bundle_binaries(bundle))
    for binary, changes in _bundle_rpath_changes(binary_list, prefix=prefix).items():
        _run_install_name_tool([*changes.delete_args, str(binary)], prefix=prefix)
        _run_install_name_tool([*changes.add_args, str(binary)], prefix=prefix)


def verify_no_homebrew_qt_resolution(bundle: Path, *, app_name: str) -> None:
    frameworks_dir = bundle / "Contents" / "Frameworks"
    executable = bundle / "Contents" / "MacOS" / app_name
    for framework in ("QtCore.framework", "QtGui.framework"):
        if not (frameworks_dir / framework).exists():
            raise PackageError(f"Missing bundled Qt framework: {framework}")
    completed = run_command(["otool", "-l", str(executable)], capture=True, prefix="deploy_mac")
    rpaths = parse_otool_rpaths(completed.stdout or "")
    if not rpaths or rpaths[0] != "@executable_path/../Frameworks":
        raise PackageError(
            "Bundle framework rpath is not first; Qt may resolve to Homebrew instead"
        )
    for rpath in rpaths:
        if "qtbase" in rpath.lower() or rpath == "/opt/homebrew/lib":
            raise PackageError(f"Unsafe Qt-resolving rpath remains in app executable: {rpath}")


def _copy_libraries_by_name(
    config: PackageConfig,
    deployed_app: Path,
    *,
    search_index: LibrarySearchIndex,
    config_key: str,
    required: bool,
    prefix: str,
) -> None:
    frameworks_dir = deployed_app / "Contents" / "Frameworks"
    ensure_dir(frameworks_dir)
    for library_name in config.optional_string_list(config_key):
        found = search_index.find(library_name)
        if found:
            copy_file(found, frameworks_dir, prefix=prefix)
        elif required:
            raise PackageError(f"Configured macOS library not found: {library_name}")
        else:
            log(f"Optional library not found, skipped: {library_name}", prefix=prefix)


def _copy_globbed_libraries(
    config: PackageConfig,
    deployed_app: Path,
    *,
    search_index: LibrarySearchIndex,
    config_key: str,
    required: bool,
    prefix: str,
) -> None:
    frameworks_dir = deployed_app / "Contents" / "Frameworks"
    ensure_dir(frameworks_dir)
    for pattern in config.optional_string_list(config_key):
        matches = search_index.matching(pattern)
        for library in matches:
            copy_file(library, frameworks_dir, prefix=prefix)
        matched = bool(matches)
        if not matched and required:
            raise PackageError(f"No macOS library matched configured pattern: {pattern}")
        if not matched and not required:
            log(f"No optional library matched pattern: {pattern}", prefix=prefix)


def _is_allowed_library_path(path: Path, library_roots: Iterable[Path]) -> bool:
    return any(path.is_relative_to(root) for root in library_roots)


def _resolve_dependency_source(
    dependency: str,
    *,
    binary_source: Path | None,
    library_roots: tuple[Path, ...],
    search_index: LibrarySearchIndex | None,
) -> Path | None:
    candidate = Path(dependency)
    if (
        candidate.is_absolute()
        and candidate.is_file()
        and _is_allowed_library_path(candidate, library_roots)
    ):
        return candidate
    if binary_source is not None:
        if dependency.startswith("@rpath/"):
            candidate = binary_source.parent / Path(dependency).name
        elif dependency.startswith("@loader_path/"):
            candidate = binary_source.parent / dependency.removeprefix("@loader_path/")
        if candidate.is_file() and _is_allowed_library_path(candidate, library_roots):
            return candidate
    if search_index is None:
        return None
    return search_index.find(candidate.name)


def _copy_dependency_closure(
    binaries: list[Path],
    *,
    frameworks_dir: Path,
    library_roots: tuple[Path, ...],
    search_index: LibrarySearchIndex | None,
    prefix: str,
) -> defaultdict[Path, list[str]]:
    first_install_name_args: defaultdict[Path, list[str]] = defaultdict(list)
    known_binaries = set(binaries)
    copied_sources: dict[Path, Path] = {}
    binary_sources: dict[Path, Path] = {binary: binary for binary in binaries}
    pending = list(binaries)

    while pending:
        binary = pending.pop(0)
        output = inspect_otool_outputs(
            [binary],
            "-L",
            prefix=prefix,
            allow_failures=True,
        )[binary]
        if output is None:
            warn(f"Unable to inspect optional library dependencies: {binary}", prefix=prefix)
            continue
        for dependency in parse_otool_deps(output):
            dependency_name = Path(dependency).name
            if not dependency_name.endswith(".dylib"):
                continue
            if dependency.startswith(SYSTEM_LIBRARY_PREFIXES):
                continue
            if dependency.startswith("@executable_path/"):
                continue
            if dependency.startswith("@rpath/") and (
                (frameworks_dir / dependency_name).is_file()
                or (
                    frameworks_dir.parent / "Resources" / "lib" / "darktable" / dependency_name
                ).is_file()
            ):
                continue
            if dependency.startswith("@") and not dependency.startswith(
                ("@loader_path/", "@rpath/")
            ):
                continue
            source = _resolve_dependency_source(
                dependency,
                binary_source=binary_sources.get(binary),
                library_roots=library_roots,
                search_index=search_index,
            )
            if source is None:
                raise PackageError(
                    f"Mach-O dependency not found in mac.librarySearchPaths: {dependency} "
                    f"(required by {binary.name})"
                )

            copied = frameworks_dir / source.name
            source_identity = source.resolve()
            previous_source = copied_sources.get(copied)
            if previous_source is not None and previous_source != source_identity:
                raise PackageError(
                    f"Conflicting macOS libraries named {source.name}: "
                    f"{previous_source} and {source_identity}"
                )
            if previous_source is None:
                copy_file(source, frameworks_dir, prefix=prefix)
                copied_sources[copied] = source_identity
            if copied not in known_binaries:
                known_binaries.add(copied)
                binaries.append(copied)
                pending.append(copied)
                binary_sources[copied] = source
            if not dependency.startswith("@"):
                first_install_name_args[binary].extend(
                    ["-change", dependency, f"@rpath/{dependency_name}"]
                )

    return first_install_name_args


def _create_dmg(
    config: PackageConfig,
    deployed_app: Path,
    *,
    dist_dir: Path,
    prefix: str,
) -> Path | None:
    output = config.optional_path("mac.dmgOutputPath")
    if output is None:
        return None
    binary_dir = config.path("paths.binaryDir").resolve()
    output = output.resolve()
    if not output.is_relative_to(binary_dir):
        raise PackageError("mac.dmgOutputPath must be inside paths.binaryDir")

    volume_name = config.required_string("mac.dmgVolumeName")
    image_root = dist_dir / ".dmg-root"
    clean_dir(image_root)
    shutil.copytree(deployed_app, image_root / deployed_app.name, symlinks=True)
    (image_root / "Applications").symlink_to("/Applications")

    background = config.optional_path("mac.dmgBackground")
    if background is not None:
        background_dir = image_root / ".background"
        copy_file(background, background_dir, prefix=prefix)
        copied_background = background_dir / background.name
        if copied_background.name != "background.png":
            copied_background.rename(background_dir / "background.png")

    ensure_dir(output.parent)
    run_command(
        [
            "hdiutil",
            "create",
            "-volname",
            volume_name,
            "-srcfolder",
            str(image_root),
            "-ov",
            "-format",
            "UDZO",
            str(output),
        ],
        prefix=prefix,
    )
    if not output.is_file():
        raise PackageError(f"hdiutil did not create expected output: {output}")
    return output


def deploy_mac(config: PackageConfig) -> Path:
    prefix = "deploy_mac"
    app_name = config.required_string("app.name")
    display_name = config.required_string("app.displayName")
    source_bundle = config.path("mac.bundlePath")
    dist_dir = config.path("paths.distDir")
    deployment_tool = config.required_string("mac.deploymentTool")
    entitlements = config.path("mac.entitlementsFile")
    sign_identity = config.optional_string("mac.signIdentity", "-") or "-"

    if not source_bundle.exists():
        raise PackageError(f"App bundle not found: {source_bundle}")
    if not entitlements.is_file():
        raise PackageError(f"Entitlements file not found: {entitlements}")

    clean_dist_dir(config, dist_dir)
    deployed_app: Path = dist_dir / f"{display_name}.app"
    shutil.copytree(source_bundle, deployed_app, symlinks=True)

    if deployment_tool == "qt":
        qt_bin_dir = config.path("qt.binDir")
        qml_dir = config.path("qt.qmlDir")
        macdeployqt = qt_bin_dir / "macdeployqt"
        run_command(
            [
                str(macdeployqt),
                str(deployed_app),
                "-verbose=1",
                f"-qmldir={qml_dir}",
                "-always-overwrite",
                "-appstore-compliant",
            ],
            prefix=prefix,
        )
    elif deployment_tool != "native":
        raise PackageError(
            "Invalid mac.deploymentTool; expected one of: "
            + ", ".join(sorted(MAC_DEPLOYMENT_TOOLS))
        )

    resources_dir = deployed_app / "Contents" / "Resources"
    frameworks_dir = deployed_app / "Contents" / "Frameworks"
    search_paths = config.optional_path_list("mac.librarySearchPaths")
    library_roots = tuple(path.resolve() for path in search_paths if path.is_dir())
    search_index: LibrarySearchIndex | None = None

    def get_search_index() -> LibrarySearchIndex:
        nonlocal search_index
        if search_index is None:
            search_index = build_library_search_index(search_paths)
        return search_index

    ensure_dir(resources_dir)
    ensure_dir(frameworks_dir)
    copy_configured_resources(config, resources_dir, prefix=prefix)

    for library in config.optional_path_list("mac.extraLibraries"):
        copy_file(library, frameworks_dir, prefix=prefix)
    for library in config.optional_path_list("mac.optionalExtraLibraries"):
        copy_file(library, frameworks_dir, required=False, prefix=prefix)
    _copy_libraries_by_name(
        config,
        deployed_app,
        search_index=get_search_index(),
        config_key="mac.copyLibraryNames",
        required=True,
        prefix=prefix,
    )
    _copy_libraries_by_name(
        config,
        deployed_app,
        search_index=get_search_index(),
        config_key="mac.optionalLibraryNames",
        required=False,
        prefix=prefix,
    )
    _copy_globbed_libraries(
        config,
        deployed_app,
        search_index=get_search_index(),
        config_key="mac.libraryGlobs",
        required=True,
        prefix=prefix,
    )
    _copy_globbed_libraries(
        config,
        deployed_app,
        search_index=get_search_index(),
        config_key="mac.optionalLibraryGlobs",
        required=False,
        prefix=prefix,
    )

    binaries = collect_bundle_binaries(deployed_app)
    first_install_name_args = _copy_dependency_closure(
        binaries,
        frameworks_dir=frameworks_dir,
        library_roots=library_roots,
        search_index=search_index,
        prefix=prefix,
    )
    second_install_name_args: defaultdict[Path, list[str]] = defaultdict(list)

    if config.optional_bool("mac.normalizeRpaths", False):
        for binary, changes in _bundle_rpath_changes(binaries, prefix=prefix).items():
            first_install_name_args[binary].extend(changes.delete_args)
            second_install_name_args[binary].extend(changes.add_args)

    for binary in binaries:
        if binary.suffix == ".dylib":
            second_install_name_args[binary].extend(["-id", f"@rpath/{binary.name}"])
        first_args = first_install_name_args.get(binary, [])
        second_args = second_install_name_args.get(binary, [])
        if first_args and second_args and "-delete_rpath" not in first_args:
            _run_install_name_tool([*first_args, *second_args, str(binary)], prefix=prefix)
            continue
        if first_args:
            _run_install_name_tool([*first_args, str(binary)], prefix=prefix)
        if second_args:
            _run_install_name_tool([*second_args, str(binary)], prefix=prefix)

    if config.optional_bool("mac.verifyBundledQt", False):
        verify_no_homebrew_qt_resolution(deployed_app, app_name=app_name)

    for binary in binaries:
        run_command(build_sign_command(binary, identity=sign_identity), prefix=prefix)

    run_command(
        build_sign_command(
            deployed_app, identity=sign_identity, entitlements=entitlements, runtime=True
        ),
        prefix=prefix,
    )
    dmg = _create_dmg(config, deployed_app, dist_dir=dist_dir, prefix=prefix)
    log(f"Deployment completed for {app_name}: {deployed_app}", prefix=prefix)
    if dmg is not None:
        log(f"DMG created: {dmg}", prefix=prefix)
    return deployed_app
