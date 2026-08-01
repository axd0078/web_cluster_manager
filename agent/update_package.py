from __future__ import annotations

import base64
import hashlib
import json
import platform
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


UPDATER_VERSION = "2.0.0"
MAX_PACKAGE_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_BYTES = 500 * 1024 * 1024
MAX_ENTRIES = 10_000

KEY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
PLATFORM_VALUES = {"windows", "linux"}
ARCH_VALUES = {"x86_64", "aarch64"}
PYTHON_ABI_RE = re.compile(r"^cp3\d{1,2}$")
ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
LOCKED_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*"
    r"(?:\[[A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*\])?"
    r"==[A-Za-z0-9][A-Za-z0-9.!+_-]*"
    r"(?:\s+--hash=sha256:[0-9a-fA-F]{64})+$"
)


class UpdatePackageError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeIdentity:
    os_family: str
    arch: str
    python_abi: str


@dataclass(frozen=True)
class VerifiedUpdatePackage:
    manifest: dict
    manifest_json: str
    package_sha256: str
    package_size: int
    expanded_size: int
    key_id: str
    version: str
    release_id: str
    target_os: str
    target_arch: str
    python_abi: str
    min_updater_version: str
    entrypoint: str


def canonical_manifest(manifest: dict) -> bytes:
    return json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_arch(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"amd64", "x86_64"}:
        return "x86_64"
    if normalized in {"arm64", "aarch64"}:
        return "aarch64"
    return normalized or "unknown"


def current_runtime_identity() -> RuntimeIdentity:
    system = platform.system().strip().lower()
    return RuntimeIdentity(
        os_family=system if system in PLATFORM_VALUES else "unknown",
        arch=normalize_arch(platform.machine()),
        python_abi=f"cp{sys.version_info.major}{sys.version_info.minor}",
    )


def validate_requirements_lock(content: bytes) -> bool:
    """Validate the small, hash-pinned requirements subset used by the helper.

    Empty locks are valid for releases without third-party dependencies. URLs,
    paths, recursive includes, constraints, indexes, markers and other pip
    options are intentionally unsupported because pip runs in a privileged
    update helper.
    """
    if len(content) > 2 * 1024 * 1024:
        raise UpdatePackageError("requirements.lock exceeds its size limit")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UpdatePackageError("requirements.lock must be UTF-8") from exc
    logical_lines: list[str] = []
    pending = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pending = f"{pending} {stripped}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        logical_lines.append(pending)
        pending = ""
    if pending:
        raise UpdatePackageError("requirements.lock has an unterminated continuation")
    for line in logical_lines:
        if not LOCKED_REQUIREMENT_RE.fullmatch(line):
            raise UpdatePackageError(
                "requirements.lock only permits exact versions with SHA-256 hashes"
            )
    return bool(logical_lines)


def _validate_wheel_archive(source) -> tuple[int, int]:
    try:
        wheel = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdatePackageError("offline wheelhouse contains an invalid wheel") from exc
    with wheel:
        infos = wheel.infolist()
        if not infos:
            raise UpdatePackageError("offline wheelhouse contains an empty wheel")
        seen: set[str] = set()
        expanded = 0
        for info in infos:
            raw_name = info.filename[:-1] if info.is_dir() else info.filename
            normalized = normalize_payload_path(raw_name)
            expected = f"{normalized}/" if info.is_dir() else normalized
            if info.filename != expected or normalized.casefold() in seen:
                raise UpdatePackageError("offline wheel contains unsafe or duplicate paths")
            seen.add(normalized.casefold())
            if info.flag_bits & 0x1 or info.compress_type not in ALLOWED_COMPRESSION:
                raise UpdatePackageError("offline wheel uses unsafe ZIP features")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(unix_mode)
            allowed_types = {0, stat.S_IFDIR} if info.is_dir() else {0, stat.S_IFREG}
            if file_type not in allowed_types:
                raise UpdatePackageError("offline wheel contains a symlink or special file")
            expanded += info.file_size
            if info.compress_size == 0 and info.file_size > 0:
                raise UpdatePackageError("offline wheel contains an invalid compressed entry")
            if info.compress_size and info.file_size / info.compress_size > 200:
                raise UpdatePackageError("offline wheel contains an excessive compression ratio")
        return len(infos), expanded


def normalize_payload_path(value: object) -> str:
    if not isinstance(value, str):
        raise UpdatePackageError("update package paths must be strings")
    raw = value
    if (
        not raw
        or raw != raw.strip()
        or len(raw) > 500
        or "\x00" in raw
        or "\\" in raw
    ):
        raise UpdatePackageError("update package contains an invalid path")
    windows = PureWindowsPath(raw)
    posix = PurePosixPath(raw)
    if windows.is_absolute() or windows.drive or posix.is_absolute():
        raise UpdatePackageError("absolute, drive and UNC paths are forbidden")
    if any(part in {"", ".", ".."} for part in posix.parts):
        raise UpdatePackageError("dot path components are forbidden")
    for part in posix.parts:
        if ":" in part:
            raise UpdatePackageError("drive names and alternate data streams are forbidden")
        if part != part.rstrip(" ."):
            raise UpdatePackageError("Windows-ambiguous trailing spaces and dots are forbidden")
        stem = part.split(".", 1)[0].upper()
        if stem in WINDOWS_RESERVED:
            raise UpdatePackageError("Windows reserved device names are forbidden")
    return "/".join(posix.parts)


def _load_public_key(path: Path) -> Ed25519PublicKey:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UpdatePackageError("trusted update key is unavailable") from exc
    try:
        key = serialization.load_pem_public_key(raw)
    except ValueError:
        try:
            decoded = base64.b64decode(raw.strip(), validate=True)
            key = Ed25519PublicKey.from_public_bytes(decoded)
        except (ValueError, TypeError) as exc:
            raise UpdatePackageError("trusted update key has an invalid format") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise UpdatePackageError("trusted update key is not Ed25519")
    return key


def _read_json_entry(archive: zipfile.ZipFile, name: str, limit: int) -> dict:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise UpdatePackageError(f"update package is missing {name}") from exc
    if info.file_size > limit:
        raise UpdatePackageError(f"{name} exceeds its size limit")
    try:
        value = json.loads(archive.read(info).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdatePackageError(f"{name} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise UpdatePackageError(f"{name} must be a JSON object")
    return value


def _validate_manifest(manifest: dict) -> tuple[list[dict], dict, str]:
    allowed = {
        "schema_version",
        "component",
        "version",
        "release_id",
        "created_at",
        "key_id",
        "target",
        "min_updater_version",
        "entrypoint",
        "files",
    }
    if set(manifest) != allowed:
        raise UpdatePackageError("manifest fields are incomplete or unsupported")
    if manifest.get("schema_version") != 1 or manifest.get("component") != "agent":
        raise UpdatePackageError("only schema version 1 Agent packages are accepted")

    version = manifest.get("version")
    min_updater = manifest.get("min_updater_version")
    release_id = manifest.get("release_id")
    key_id = manifest.get("key_id")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise UpdatePackageError("Agent version must be semantic")
    if not isinstance(min_updater, str) or not VERSION_RE.fullmatch(min_updater):
        raise UpdatePackageError("minimum updater version is invalid")
    if not isinstance(release_id, str) or not RELEASE_ID_RE.fullmatch(release_id):
        raise UpdatePackageError("release ID is invalid")
    if not isinstance(key_id, str) or not KEY_ID_RE.fullmatch(key_id):
        raise UpdatePackageError("signature key ID is invalid")
    created_at = manifest.get("created_at")
    if not isinstance(created_at, str) or not created_at or len(created_at) > 64:
        raise UpdatePackageError("created_at is invalid")

    target = manifest.get("target")
    if not isinstance(target, dict) or set(target) != {"os", "arch", "python_abi"}:
        raise UpdatePackageError("manifest requires an exact target environment")
    target_os = target.get("os")
    target_arch = normalize_arch(str(target.get("arch") or ""))
    python_abi = target.get("python_abi")
    if target_os not in PLATFORM_VALUES:
        raise UpdatePackageError("target OS must be windows or linux")
    if target_arch not in ARCH_VALUES:
        raise UpdatePackageError("target CPU architecture is unsupported")
    if not isinstance(python_abi, str) or not PYTHON_ABI_RE.fullmatch(python_abi):
        raise UpdatePackageError("target Python ABI is invalid")
    normalized_target = {
        "os": target_os,
        "arch": target_arch,
        "python_abi": python_abi,
    }

    entrypoint = normalize_payload_path(manifest.get("entrypoint"))
    if not entrypoint.endswith(".py"):
        raise UpdatePackageError("Agent entrypoint must be a Python source file")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise UpdatePackageError("manifest must contain files")
    normalized_files: list[dict] = []
    seen: set[str] = set()
    seen_casefold: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise UpdatePackageError("manifest file records are invalid")
        path = normalize_payload_path(item.get("path"))
        if path in seen or path.casefold() in seen_casefold:
            raise UpdatePackageError("manifest contains duplicate files")
        seen.add(path)
        seen_casefold.add(path.casefold())
        size = item.get("size")
        digest = item.get("sha256")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise UpdatePackageError("manifest file size is invalid")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise UpdatePackageError("manifest file SHA-256 is invalid")
        normalized_files.append({"path": path, "size": size, "sha256": digest})
    if entrypoint not in seen:
        raise UpdatePackageError("Agent entrypoint is not listed in the manifest")
    if "requirements.lock" not in seen:
        raise UpdatePackageError("offline dependency lockfile is missing")
    wheel_paths = [path for path in seen if path.startswith("wheelhouse/")]
    if any(not path.lower().endswith(".whl") for path in wheel_paths):
        raise UpdatePackageError("the offline wheelhouse may contain only wheel files")
    return normalized_files, normalized_target, entrypoint


def _semver_core(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value)
    if not match:
        raise UpdatePackageError("updater version is invalid")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def verify_update_package(
    path: Path,
    trusted_keys_dir: Path,
    *,
    max_package_bytes: int = MAX_PACKAGE_BYTES,
    max_expanded_bytes: int = MAX_EXPANDED_BYTES,
    max_entries: int = MAX_ENTRIES,
    runtime: RuntimeIdentity | None = None,
    updater_version: str = UPDATER_VERSION,
) -> VerifiedUpdatePackage:
    try:
        package_info = path.lstat()
    except OSError as exc:
        raise UpdatePackageError("update package does not exist") from exc
    if not stat.S_ISREG(package_info.st_mode) or path.is_symlink():
        raise UpdatePackageError("update package must be a regular file")
    package_size = package_info.st_size
    if package_size <= 0 or package_size > max_package_bytes:
        raise UpdatePackageError("update package is empty or exceeds its size limit")
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdatePackageError("update package is not a valid ZIP archive") from exc

    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > max_entries:
            raise UpdatePackageError("update package entry count is invalid")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or len(names) != len({name.casefold() for name in names}):
            raise UpdatePackageError("update package contains duplicate ZIP paths")
        expanded_size = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise UpdatePackageError("encrypted ZIP entries are forbidden")
            if info.is_dir():
                raise UpdatePackageError("directory ZIP entries are forbidden")
            if info.compress_type not in ALLOWED_COMPRESSION:
                raise UpdatePackageError("unsupported ZIP compression method")
            normalized = normalize_payload_path(info.filename)
            if normalized != info.filename:
                raise UpdatePackageError("ZIP paths must use canonical forward slashes")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(unix_mode)
            if file_type not in {0, stat.S_IFREG}:
                raise UpdatePackageError("symlink and special-file entries are forbidden")
            expanded_size += info.file_size
            if expanded_size > max_expanded_bytes:
                raise UpdatePackageError("expanded update package exceeds its size limit")
            if info.compress_size == 0 and info.file_size > 0:
                raise UpdatePackageError("update package contains an invalid compressed entry")
            if info.compress_size and info.file_size / info.compress_size > 200:
                raise UpdatePackageError("update package contains an excessive compression ratio")

        manifest = _read_json_entry(archive, "manifest.json", 1024 * 1024)
        files, target, entrypoint = _validate_manifest(manifest)
        key_id = str(manifest["key_id"])
        expected_names = {
            "manifest.json",
            "manifest.sig",
            *(f"payload/{item['path']}" for item in files),
        }
        if set(names) != expected_names:
            raise UpdatePackageError("package has unlisted files or is missing listed files")
        try:
            signature_info = archive.getinfo("manifest.sig")
            if signature_info.file_size > 1024:
                raise UpdatePackageError("manifest signature exceeds its size limit")
            signature = base64.b64decode(
                archive.read(signature_info).strip(),
                validate=True,
            )
        except (KeyError, ValueError) as exc:
            raise UpdatePackageError("manifest signature encoding is invalid") from exc
        if len(signature) != 64:
            raise UpdatePackageError("manifest signature length is invalid")
        public_key = _load_public_key(trusted_keys_dir / f"{key_id}.pub")
        canonical = canonical_manifest(manifest)
        try:
            public_key.verify(signature, canonical)
        except InvalidSignature as exc:
            raise UpdatePackageError("Ed25519 manifest signature verification failed") from exc

        for item in files:
            info = archive.getinfo(f"payload/{item['path']}")
            if info.file_size != item["size"]:
                raise UpdatePackageError(f"file size does not match manifest: {item['path']}")
            digest = hashlib.sha256()
            with archive.open(info) as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != item["sha256"]:
                raise UpdatePackageError(f"file hash does not match manifest: {item['path']}")

        lock_info = archive.getinfo("payload/requirements.lock")
        if lock_info.file_size > 2 * 1024 * 1024:
            raise UpdatePackageError("requirements.lock exceeds its size limit")
        has_dependencies = validate_requirements_lock(archive.read(lock_info))
        wheel_paths = [
            item["path"] for item in files
            if item["path"].startswith("wheelhouse/")
        ]
        if has_dependencies and not wheel_paths:
            raise UpdatePackageError("locked dependencies require an offline wheelhouse")
        nested_entries = 0
        nested_expanded = 0
        for wheel_path in wheel_paths:
            wheel_info = archive.getinfo(f"payload/{wheel_path}")
            with tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024) as staged_wheel:
                with archive.open(wheel_info) as wheel_source:
                    shutil.copyfileobj(wheel_source, staged_wheel, length=1024 * 1024)
                staged_wheel.seek(0)
                entry_count, wheel_expanded = _validate_wheel_archive(staged_wheel)
            nested_entries += entry_count
            nested_expanded += wheel_expanded
            if len(infos) + nested_entries > max_entries:
                raise UpdatePackageError("nested wheel entries exceed the package file limit")
            if expanded_size + nested_expanded > max_expanded_bytes:
                raise UpdatePackageError("nested wheels exceed the expanded package size limit")
        expanded_size += nested_expanded

    if _semver_core(str(manifest["min_updater_version"])) > _semver_core(updater_version):
        raise UpdatePackageError("package requires a newer privileged updater")
    if runtime is not None:
        if (
            target["os"] != runtime.os_family
            or target["arch"] != runtime.arch
            or target["python_abi"] != runtime.python_abi
        ):
            raise UpdatePackageError("package target does not match this Agent runtime")

    return VerifiedUpdatePackage(
        manifest=manifest,
        manifest_json=canonical_manifest(manifest).decode("utf-8"),
        package_sha256=sha256_file(path),
        package_size=package_size,
        expanded_size=expanded_size,
        key_id=str(manifest["key_id"]),
        version=str(manifest["version"]),
        release_id=str(manifest["release_id"]),
        target_os=str(target["os"]),
        target_arch=str(target["arch"]),
        python_abi=str(target["python_abi"]),
        min_updater_version=str(manifest["min_updater_version"]),
        entrypoint=entrypoint,
    )
