#!/usr/bin/env python3
"""Build and verify signed Web Cluster Manager Agent update packages.

The signing private key is intentionally required to live outside this
repository. Runtime systems only receive the corresponding public key.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PASSWORD_ENV = "WCM_UPDATE_SIGNING_KEY_PASSWORD"
VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
KEY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PYTHON_ABI_RE = re.compile(r"^cp3\d{1,2}$")
TARGET_OS_VALUES = {"windows", "linux"}
TARGET_ARCH_VALUES = {"x86_64", "aarch64"}
TARGET_ARCH_ALIASES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "aarch64",
    "arm64": "aarch64",
}
IGNORED_SOURCE_DIRECTORIES = {
    ".git",
    ".venv",
    "__pycache__",
    "agent_data",
    "backup",
    "log",
    "logs",
    "venv",
}
MANIFEST_FIELDS = {
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
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
LOCKED_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]*"
    r"(?:\[[A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*\])?"
    r"==[A-Za-z0-9][A-Za-z0-9.!+_-]*"
    r"(?:\s+--hash=sha256:[0-9a-fA-F]{64})+$"
)


class PackageError(ValueError):
    """A safe, user-facing package validation error."""


@dataclass(frozen=True)
class PayloadFile:
    path: str
    source: Path
    size: int
    sha256: str
    executable: bool


def canonical_json(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_stream(source) -> str:
    digest = hashlib.sha256()
    while chunk := source.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as source:
        return sha256_stream(source)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _is_reparse_point(path: Path) -> bool:
    try:
        value = path.lstat()
    except OSError as exc:
        raise PackageError(f"无法读取路径属性：{path}") from exc
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _require_private_key_outside_repository(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    if _is_within(resolved, REPOSITORY_ROOT):
        raise PackageError("签名私钥必须保存在项目仓库之外")
    return resolved


def normalize_payload_path(value: object) -> str:
    if not isinstance(value, str):
        raise PackageError("文件路径必须是字符串")
    raw = value
    if (
        not raw
        or raw != raw.strip()
        or len(raw) > 500
        or "\x00" in raw
        or "\\" in raw
    ):
        raise PackageError(f"非法 payload 路径：{value!r}")
    windows = PureWindowsPath(raw)
    posix = PurePosixPath(raw)
    if windows.is_absolute() or windows.drive or posix.is_absolute():
        raise PackageError(f"payload 路径不能是绝对路径、盘符或 UNC：{raw}")
    for part in posix.parts:
        if (
            part in {"", ".", ".."}
            or ":" in part
            or part.endswith((" ", "."))
            or any(ord(character) < 32 or character in '<>"|?*' for character in part)
            or part.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
        ):
            raise PackageError(f"payload 路径包含非法或 Windows 保留分段：{raw}")
    return "/".join(posix.parts)


def normalize_target_arch(value: str) -> str:
    try:
        return TARGET_ARCH_ALIASES[value.strip().lower()]
    except KeyError as exc:
        raise PackageError("目标架构必须是 x86_64/amd64/aarch64/arm64") from exc


def _password_from_environment(name: str, *, required: bool) -> bytes | None:
    value = os.environ.get(name)
    if value is None:
        if required:
            raise PackageError(f"必须通过环境变量 {name} 提供签名私钥口令")
        return None
    encoded = value.encode("utf-8")
    if required and len(encoded) < 16:
        raise PackageError(f"{name} 至少需要 16 个 UTF-8 字节")
    return encoded


def _key_id(public_key: Ed25519PublicKey) -> str:
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()[:32]


def _public_pem(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _load_private_key(path: Path, password_env: str) -> Ed25519PrivateKey:
    resolved = _require_private_key_outside_repository(path)
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise PackageError("无法读取签名私钥") from exc
    password = _password_from_environment(password_env, required=False)
    attempts: list[bytes | None] = [password] if password is not None else [None]
    if password is not None:
        attempts.append(None)
    key = None
    for candidate in attempts:
        try:
            key = serialization.load_pem_private_key(raw, password=candidate)
            break
        except (TypeError, ValueError):
            continue
    if not isinstance(key, Ed25519PrivateKey):
        raise PackageError(
            f"无法加载 Ed25519 私钥；请检查私钥格式和 {password_env}"
        )
    return key


def _load_public_key(path: Path) -> Ed25519PublicKey:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PackageError(f"无法读取可信公钥：{path}") from exc
    try:
        key = serialization.load_pem_public_key(raw)
    except ValueError:
        try:
            decoded = base64.b64decode(raw.strip(), validate=True)
            key = Ed25519PublicKey.from_public_bytes(decoded)
        except (TypeError, ValueError) as exc:
            raise PackageError(f"公钥格式无效：{path}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise PackageError(f"公钥不是 Ed25519：{path}")
    return key


def _atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as destination:
            destination.write(data)
            destination.flush()
            os.fsync(destination.fileno())
        if mode is not None:
            try:
                temporary.chmod(mode)
            except OSError:
                pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def generate_keypair(
    private_key_path: Path,
    public_key_dir: Path,
    password_env: str,
) -> tuple[str, Path]:
    private_path = _require_private_key_outside_repository(private_key_path)
    if private_path.exists():
        raise PackageError("拒绝覆盖已有签名私钥")
    password = _password_from_environment(password_env, required=True)
    assert password is not None
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    key_id = _key_id(public_key)
    public_path = public_key_dir.expanduser().resolve() / f"{key_id}.pub"
    if public_path.exists():
        raise PackageError(f"拒绝覆盖已有公钥：{public_path}")
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(password),
    )
    _atomic_write(private_path, private_pem, 0o600)
    try:
        _atomic_write(public_path, _public_pem(public_key), 0o644)
    except Exception:
        private_path.unlink(missing_ok=True)
        raise
    return key_id, public_path


def export_public_key(
    private_key_path: Path,
    public_key_dir: Path,
    password_env: str,
) -> tuple[str, Path]:
    private_key = _load_private_key(private_key_path, password_env)
    public_key = private_key.public_key()
    key_id = _key_id(public_key)
    output = public_key_dir.expanduser().resolve() / f"{key_id}.pub"
    pem = _public_pem(public_key)
    if output.exists() and output.read_bytes() != pem:
        raise PackageError(f"拒绝覆盖内容不同的公钥：{output}")
    if not output.exists():
        _atomic_write(output, pem, 0o644)
    return key_id, output


def _validate_regular_file(path: Path) -> os.stat_result:
    if _is_reparse_point(path):
        raise PackageError(f"拒绝符号链接或重解析点：{path}")
    try:
        value = path.stat()
    except OSError as exc:
        raise PackageError(f"无法读取源文件：{path}") from exc
    if not stat.S_ISREG(value.st_mode):
        raise PackageError(f"只允许普通文件：{path}")
    return value


def _walk_regular_files(root: Path) -> Iterable[tuple[str, Path, os.stat_result]]:
    root = root.expanduser().resolve()
    if not root.is_dir() or _is_reparse_point(root):
        raise PackageError(f"源目录不存在或不是安全的普通目录：{root}")

    def walk(directory: Path, prefix: tuple[str, ...]):
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise PackageError(f"无法枚举源目录：{directory}") from exc
        for entry in entries:
            candidate = Path(entry.path)
            if entry.is_symlink() or _is_reparse_point(candidate):
                raise PackageError(f"拒绝符号链接或重解析点：{candidate}")
            try:
                if (
                    entry.name in IGNORED_SOURCE_DIRECTORIES
                    and entry.is_dir(follow_symlinks=False)
                ):
                    continue
                if (
                    entry.name.endswith((".pyc", ".pyo"))
                    and entry.is_file(follow_symlinks=False)
                ):
                    continue
                relative = normalize_payload_path(
                    PurePosixPath(*prefix, entry.name).as_posix()
                )
                if entry.is_dir(follow_symlinks=False):
                    yield from walk(candidate, (*prefix, entry.name))
                elif entry.is_file(follow_symlinks=False):
                    info = _validate_regular_file(candidate)
                    yield relative, candidate, info
                else:
                    raise PackageError(f"只允许普通文件和目录：{candidate}")
            except OSError as exc:
                raise PackageError(f"无法检查源路径：{candidate}") from exc

    yield from walk(root, ())


def _payload_file(path: str, source: Path, info: os.stat_result) -> PayloadFile:
    return PayloadFile(
        path=normalize_payload_path(path),
        source=source,
        size=info.st_size,
        sha256=sha256_file(source),
        executable=bool(info.st_mode & 0o111),
    )


def _validate_requirements_lock_content(content: bytes) -> None:
    if not content or len(content) > 2 * 1024 * 1024:
        raise PackageError("requirements.lock 为空或超过 2 MiB")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackageError("requirements.lock 必须是 UTF-8 文本") from exc
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
        raise PackageError("requirements.lock 包含未结束的续行")
    if not logical_lines:
        raise PackageError("requirements.lock 没有锁定依赖")
    for line in logical_lines:
        if not LOCKED_REQUIREMENT_RE.fullmatch(line):
            raise PackageError(
                "requirements.lock 每个依赖都必须使用 == 精确版本和 SHA-256 哈希"
            )


def _validate_requirements_lock(path: Path, info: os.stat_result) -> None:
    if info.st_size <= 0 or info.st_size > 2 * 1024 * 1024:
        raise PackageError("requirements.lock 为空或超过 2 MiB")
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise PackageError("无法读取 requirements.lock") from exc
    _validate_requirements_lock_content(content)


def _validate_wheel_archive(source) -> tuple[int, int]:
    try:
        wheel = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackageError("wheelhouse 包含无效 wheel") from exc
    with wheel:
        infos = wheel.infolist()
        if not infos:
            raise PackageError("wheelhouse 包含空 wheel")
        seen: set[str] = set()
        expanded = 0
        for info in infos:
            raw_name = info.filename[:-1] if info.is_dir() else info.filename
            normalized = normalize_payload_path(raw_name)
            expected = f"{normalized}/" if info.is_dir() else normalized
            if info.filename != expected or normalized.casefold() in seen:
                raise PackageError("wheel 包含危险或重复路径")
            seen.add(normalized.casefold())
            if info.flag_bits & 0x1 or info.compress_type not in {
                zipfile.ZIP_STORED,
                zipfile.ZIP_DEFLATED,
            }:
                raise PackageError("wheel 使用不安全的 ZIP 特性")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(unix_mode)
            allowed_types = {0, stat.S_IFDIR} if info.is_dir() else {0, stat.S_IFREG}
            if file_type not in allowed_types:
                raise PackageError("wheel 包含符号链接或特殊文件")
            expanded += info.file_size
            if info.compress_size == 0 and info.file_size > 0:
                raise PackageError("wheel 包含异常压缩条目")
            if info.compress_size and info.file_size / info.compress_size > 200:
                raise PackageError("wheel 包含异常压缩比条目")
        return len(infos), expanded


def collect_payload(
    source_root: Path,
    requirements_lock: Path,
    wheelhouse: Path,
) -> list[PayloadFile]:
    payload: dict[str, PayloadFile] = {}
    payload_casefold: set[str] = set()

    def add(item: PayloadFile) -> None:
        if item.path in payload:
            raise PackageError(f"payload 路径冲突：{item.path}")
        folded = item.path.casefold()
        if folded in payload_casefold:
            raise PackageError(f"payload 包含 Windows 大小写冲突：{item.path}")
        payload[item.path] = item
        payload_casefold.add(folded)

    for relative, source, info in _walk_regular_files(source_root):
        add(_payload_file(relative, source, info))

    lock = requirements_lock.expanduser().resolve()
    lock_info = _validate_regular_file(lock)
    _validate_requirements_lock(lock, lock_info)
    add(_payload_file("requirements.lock", lock, lock_info))

    wheel_count = 0
    for relative, source, info in _walk_regular_files(wheelhouse):
        if not relative.lower().endswith(".whl"):
            raise PackageError(f"wheelhouse 只能包含 .whl 文件：{relative}")
        add(_payload_file(f"wheelhouse/{relative}", source, info))
        wheel_count += 1
    if wheel_count == 0:
        raise PackageError("wheelhouse 至少需要一个 .whl 文件")
    return [payload[name] for name in sorted(payload)]


def _validate_semver(value: str, label: str) -> None:
    if not VERSION_RE.fullmatch(value):
        raise PackageError(f"{label} 必须是有效的语义化版本")


def _manifest(
    *,
    payload: list[PayloadFile],
    private_key: Ed25519PrivateKey,
    version: str,
    release_id: str,
    target_os: str,
    target_arch: str,
    python_abi: str,
    min_updater_version: str,
    entrypoint: str,
) -> dict:
    _validate_semver(version, "Agent 版本")
    _validate_semver(min_updater_version, "最低更新助手版本")
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise PackageError("release ID 格式无效")
    if target_os not in TARGET_OS_VALUES:
        raise PackageError("目标操作系统必须是 windows 或 linux")
    target_arch = normalize_target_arch(target_arch)
    if not PYTHON_ABI_RE.fullmatch(python_abi):
        raise PackageError("Python ABI 必须采用 cp310、cp311 等格式")
    normalized_entrypoint = normalize_payload_path(entrypoint)
    if not normalized_entrypoint.endswith(".py"):
        raise PackageError("entrypoint 必须是 Python 源文件")
    paths = {item.path for item in payload}
    if normalized_entrypoint not in paths:
        raise PackageError("entrypoint 必须是 Agent 源目录中已打包的文件")
    return {
        "schema_version": 1,
        "component": "agent",
        "version": version,
        "release_id": release_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "key_id": _key_id(private_key.public_key()),
        "target": {
            "os": target_os,
            "arch": target_arch,
            "python_abi": python_abi,
        },
        "min_updater_version": min_updater_version,
        "entrypoint": normalized_entrypoint,
        "files": [
            {"path": item.path, "size": item.size, "sha256": item.sha256}
            for item in payload
        ],
    }


def _zip_info(name: str, *, executable: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    permissions = 0o755 if executable else 0o644
    info.external_attr = (stat.S_IFREG | permissions) << 16
    info.compress_type = zipfile.ZIP_STORED
    return info


def build_package(
    *,
    source_root: Path,
    output: Path,
    private_key_path: Path,
    password_env: str,
    requirements_lock: Path,
    wheelhouse: Path,
    version: str,
    release_id: str,
    target_os: str,
    target_arch: str,
    python_abi: str,
    min_updater_version: str,
    entrypoint: str,
) -> dict:
    output = output.expanduser().resolve()
    if output.suffix.lower() != ".wcmupd":
        raise PackageError("输出文件扩展名必须是 .wcmupd")
    source_root = source_root.expanduser().resolve()
    if _is_within(output, source_root):
        raise PackageError("更新包输出路径不能位于 Agent 源目录内")
    if output.exists():
        raise PackageError("拒绝覆盖已有更新包")
    private_key = _load_private_key(private_key_path, password_env)
    payload = collect_payload(source_root, requirements_lock, wheelhouse)
    manifest = _manifest(
        payload=payload,
        private_key=private_key,
        version=version,
        release_id=release_id,
        target_os=target_os,
        target_arch=target_arch,
        python_abi=python_abi,
        min_updater_version=min_updater_version,
        entrypoint=entrypoint,
    )
    manifest_bytes = canonical_json(manifest)
    signature = base64.b64encode(private_key.sign(manifest_bytes))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(temporary, mode="x", allowZip64=True) as archive:
            archive.writestr(_zip_info("manifest.json"), manifest_bytes)
            archive.writestr(_zip_info("manifest.sig"), signature)
            for item in payload:
                with item.source.open("rb") as source, archive.open(
                    _zip_info(f"payload/{item.path}", executable=item.executable),
                    mode="w",
                    force_zip64=True,
                ) as destination:
                    while chunk := source.read(1024 * 1024):
                        destination.write(chunk)
        verified = verify_package(
            temporary,
            public_key=private_key.public_key(),
            expected_key_id=str(manifest["key_id"]),
        )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        **verified,
        "path": str(output),
        "package_sha256": sha256_file(output),
        "package_size": output.stat().st_size,
    }


def _validate_manifest(manifest: object) -> tuple[list[dict], str]:
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_FIELDS:
        raise PackageError("manifest.json 字段不完整或包含未知字段")
    if manifest.get("schema_version") != 1 or manifest.get("component") != "agent":
        raise PackageError("只接受 schema version 1 的 Agent 更新包")
    version = manifest.get("version")
    minimum = manifest.get("min_updater_version")
    if not isinstance(version, str):
        raise PackageError("Agent 版本格式无效")
    if not isinstance(minimum, str):
        raise PackageError("最低更新助手版本格式无效")
    _validate_semver(version, "Agent 版本")
    _validate_semver(minimum, "最低更新助手版本")
    release_id = manifest.get("release_id")
    if not isinstance(release_id, str) or not RELEASE_ID_RE.fullmatch(release_id):
        raise PackageError("release ID 格式无效")
    created_at = manifest.get("created_at")
    if not isinstance(created_at, str) or not created_at or len(created_at) > 64:
        raise PackageError("创建时间格式无效")
    key_id = manifest.get("key_id")
    if not isinstance(key_id, str) or not KEY_ID_RE.fullmatch(key_id):
        raise PackageError("签名 key ID 格式无效")
    target = manifest.get("target")
    if not isinstance(target, dict) or set(target) != {"os", "arch", "python_abi"}:
        raise PackageError("目标环境字段无效")
    if target.get("os") not in TARGET_OS_VALUES:
        raise PackageError("目标操作系统无效")
    if target.get("arch") not in TARGET_ARCH_VALUES:
        raise PackageError("目标 CPU 架构无效")
    python_abi = target.get("python_abi")
    if not isinstance(python_abi, str) or not PYTHON_ABI_RE.fullmatch(python_abi):
        raise PackageError("目标 Python ABI 无效")
    entrypoint = normalize_payload_path(manifest.get("entrypoint"))
    if not entrypoint.endswith(".py"):
        raise PackageError("entrypoint 必须是 Python 源文件")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise PackageError("文件清单不能为空")
    normalized: list[dict] = []
    seen: set[str] = set()
    seen_casefold: set[str] = set()
    for value in files:
        if not isinstance(value, dict) or set(value) != {"path", "size", "sha256"}:
            raise PackageError("文件记录字段无效")
        path = normalize_payload_path(value.get("path"))
        size = value.get("size")
        digest = value.get("sha256")
        if path in seen:
            raise PackageError("文件清单包含重复路径")
        if path.casefold() in seen_casefold:
            raise PackageError("文件清单包含 Windows 大小写冲突路径")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise PackageError("文件大小无效")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise PackageError("文件 SHA-256 无效")
        seen.add(path)
        seen_casefold.add(path.casefold())
        normalized.append({"path": path, "size": size, "sha256": digest})
    if entrypoint not in seen:
        raise PackageError("entrypoint 不在文件清单中")
    if "requirements.lock" not in seen:
        raise PackageError("更新包缺少 requirements.lock")
    wheel_paths = [path for path in seen if path.startswith("wheelhouse/")]
    if not wheel_paths:
        raise PackageError("更新包缺少离线 wheelhouse")
    if any(not path.lower().endswith(".whl") for path in wheel_paths):
        raise PackageError("更新包离线 wheelhouse 只能包含 .whl 文件")
    return normalized, key_id


def verify_package(
    package: Path,
    *,
    trusted_keys_dir: Path | None = None,
    public_key: Ed25519PublicKey | None = None,
    expected_key_id: str | None = None,
    max_package_bytes: int = 100 * 1024 * 1024,
    max_expanded_bytes: int = 500 * 1024 * 1024,
    max_entries: int = 10_000,
) -> dict:
    package = package.expanduser().resolve()
    try:
        package_size = package.stat().st_size
    except OSError as exc:
        raise PackageError("更新包不存在") from exc
    if package_size <= 0 or package_size > max_package_bytes:
        raise PackageError("更新包为空或超过大小限制")
    try:
        archive_context = zipfile.ZipFile(package)
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackageError("更新包不是有效 ZIP 文件") from exc
    with archive_context as archive:
        infos = archive.infolist()
        if not infos or len(infos) > max_entries:
            raise PackageError("ZIP 条目为空或超过数量限制")
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            raise PackageError("ZIP 包含重复路径")
        expanded_size = 0
        for info in infos:
            if info.flag_bits & 0x1 or info.is_dir():
                raise PackageError("ZIP 不能包含加密条目或目录条目")
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise PackageError("ZIP 使用了不支持的压缩方法")
            if normalize_payload_path(info.filename) != info.filename:
                raise PackageError("ZIP 路径不是规范的正斜杠形式")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(unix_mode)
            if file_type not in {0, stat.S_IFREG}:
                raise PackageError("ZIP 不能包含符号链接或特殊文件")
            expanded_size += info.file_size
            if expanded_size > max_expanded_bytes:
                raise PackageError("ZIP 展开后超过大小限制")
            if info.compress_size == 0 and info.file_size > 0:
                raise PackageError("ZIP 包含异常压缩条目")
            if info.compress_size and info.file_size / info.compress_size > 200:
                raise PackageError("ZIP 包含异常压缩比条目")
        try:
            manifest_info = archive.getinfo("manifest.json")
            signature_info = archive.getinfo("manifest.sig")
        except KeyError as exc:
            raise PackageError("更新包缺少 manifest.json 或 manifest.sig") from exc
        if manifest_info.file_size > 1024 * 1024 or signature_info.file_size > 256:
            raise PackageError("清单或签名超过大小限制")
        try:
            manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PackageError("manifest.json 不是有效 UTF-8 JSON") from exc
        files, key_id = _validate_manifest(manifest)
        expected_names = {
            "manifest.json",
            "manifest.sig",
            *(f"payload/{item['path']}" for item in files),
        }
        if set(names) != expected_names:
            raise PackageError("ZIP 包含未登记文件或缺少 payload 文件")
        try:
            signature = base64.b64decode(
                archive.read(signature_info).strip(),
                validate=True,
            )
        except ValueError as exc:
            raise PackageError("manifest.sig 不是有效 Base64") from exc
        if len(signature) != 64:
            raise PackageError("manifest.sig 长度无效")
        if expected_key_id is not None and key_id != expected_key_id:
            raise PackageError("清单 key ID 与签名私钥不一致")
        if public_key is None:
            if trusted_keys_dir is None:
                raise PackageError("必须提供可信公钥目录")
            public_key = _load_public_key(
                trusted_keys_dir.expanduser().resolve() / f"{key_id}.pub"
            )
        if _key_id(public_key) != key_id:
            raise PackageError("可信公钥内容与清单 key ID 不一致")
        try:
            public_key.verify(signature, canonical_json(manifest))
        except InvalidSignature as exc:
            raise PackageError("Ed25519 签名验证失败") from exc
        for item in files:
            info = archive.getinfo(f"payload/{item['path']}")
            if info.file_size != item["size"]:
                raise PackageError(f"文件大小不匹配：{item['path']}")
            with archive.open(info) as source:
                actual_digest = sha256_stream(source)
            if actual_digest != item["sha256"]:
                raise PackageError(f"文件 SHA-256 不匹配：{item['path']}")
        lock_info = archive.getinfo("payload/requirements.lock")
        if lock_info.file_size > 2 * 1024 * 1024:
            raise PackageError("requirements.lock 超过 2 MiB")
        _validate_requirements_lock_content(archive.read(lock_info))
        nested_entries = 0
        nested_expanded = 0
        wheel_paths = [
            item["path"] for item in files
            if item["path"].startswith("wheelhouse/")
        ]
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
                raise PackageError("wheel 内部文件数量超过限制")
            if expanded_size + nested_expanded > max_expanded_bytes:
                raise PackageError("wheel 展开后超过更新包大小限制")
        expanded_size += nested_expanded
    return {
        "valid": True,
        "key_id": key_id,
        "version": manifest["version"],
        "release_id": manifest["release_id"],
        "target": manifest["target"],
        "file_count": len(files),
        "expanded_size": expanded_size,
    }


def _json_output(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成和验证可信 Agent .wcmupd 离线更新包",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    keygen = subcommands.add_parser(
        "keygen",
        help="在仓库外生成口令加密的 Ed25519 私钥，并输出可信公钥",
    )
    keygen.add_argument("--private-key", type=Path, required=True)
    keygen.add_argument("--public-key-dir", type=Path, required=True)
    keygen.add_argument("--password-env", default=DEFAULT_PASSWORD_ENV)

    export = subcommands.add_parser(
        "export-public-key",
        help="从仓库外的私钥重新导出 <key_id>.pub",
    )
    export.add_argument("--private-key", type=Path, required=True)
    export.add_argument("--public-key-dir", type=Path, required=True)
    export.add_argument("--password-env", default=DEFAULT_PASSWORD_ENV)

    build = subcommands.add_parser("build", help="构建并自验 .wcmupd 更新包")
    build.add_argument("--source", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--private-key", type=Path, required=True)
    build.add_argument("--password-env", default=DEFAULT_PASSWORD_ENV)
    build.add_argument("--requirements-lock", type=Path, required=True)
    build.add_argument("--wheelhouse", type=Path, required=True)
    build.add_argument("--version", required=True)
    build.add_argument("--release-id", default=None)
    build.add_argument("--target-os", choices=sorted(TARGET_OS_VALUES), required=True)
    build.add_argument(
        "--target-arch",
        choices=sorted(TARGET_ARCH_ALIASES),
        required=True,
    )
    build.add_argument("--python-abi", required=True)
    build.add_argument("--min-updater-version", default="1.0.0")
    build.add_argument("--entrypoint", default="main.py")

    verify = subcommands.add_parser("verify", help="使用可信公钥完整验证更新包")
    verify.add_argument("--package", type=Path, required=True)
    verify.add_argument("--trusted-keys-dir", type=Path, required=True)
    verify.add_argument("--max-package-bytes", type=int, default=100 * 1024 * 1024)
    verify.add_argument("--max-expanded-bytes", type=int, default=500 * 1024 * 1024)
    verify.add_argument("--max-entries", type=int, default=10_000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "keygen":
            key_id, public_path = generate_keypair(
                args.private_key,
                args.public_key_dir,
                args.password_env,
            )
            _json_output(
                {
                    "key_id": key_id,
                    "private_key": str(args.private_key.expanduser().resolve()),
                    "public_key": str(public_path),
                }
            )
        elif args.command == "export-public-key":
            key_id, public_path = export_public_key(
                args.private_key,
                args.public_key_dir,
                args.password_env,
            )
            _json_output({"key_id": key_id, "public_key": str(public_path)})
        elif args.command == "build":
            result = build_package(
                source_root=args.source,
                output=args.output,
                private_key_path=args.private_key,
                password_env=args.password_env,
                requirements_lock=args.requirements_lock,
                wheelhouse=args.wheelhouse,
                version=args.version,
                release_id=args.release_id or uuid.uuid4().hex,
                target_os=args.target_os,
                target_arch=args.target_arch,
                python_abi=args.python_abi,
                min_updater_version=args.min_updater_version,
                entrypoint=args.entrypoint,
            )
            _json_output(result)
        elif args.command == "verify":
            if (
                args.max_package_bytes <= 0
                or args.max_expanded_bytes <= 0
                or args.max_entries <= 0
            ):
                raise PackageError("验证限制必须是正整数")
            result = verify_package(
                args.package,
                trusted_keys_dir=args.trusted_keys_dir,
                max_package_bytes=args.max_package_bytes,
                max_expanded_bytes=args.max_expanded_bytes,
                max_entries=args.max_entries,
            )
            result.update(
                {
                    "package_sha256": sha256_file(args.package),
                    "package_size": args.package.stat().st_size,
                }
            )
            _json_output(result)
        else:
            raise PackageError("未知命令")
    except PackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("error: 操作已取消", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
