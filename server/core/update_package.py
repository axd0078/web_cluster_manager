from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


KEY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
PLATFORM_VALUES = {"windows", "linux"}
ARCH_VALUES = {"x86_64", "amd64", "aarch64", "arm64"}
PYTHON_ABI_RE = re.compile(r"^cp3\d{1,2}$")
ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
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


def validate_requirements_lock(content: bytes) -> bool:
    if len(content) > 2 * 1024 * 1024:
        raise UpdatePackageError("requirements.lock 超过允许大小")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UpdatePackageError("requirements.lock 必须是 UTF-8 文本") from exc
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
        raise UpdatePackageError("requirements.lock 包含未结束的续行")
    if any(not LOCKED_REQUIREMENT_RE.fullmatch(line) for line in logical_lines):
        raise UpdatePackageError("requirements.lock 只允许精确版本和 SHA-256 哈希")
    return bool(logical_lines)


def _validate_wheel_archive(source) -> tuple[int, int]:
    try:
        wheel = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdatePackageError("离线 wheelhouse 包含无效 wheel") from exc
    with wheel:
        infos = wheel.infolist()
        if not infos:
            raise UpdatePackageError("离线 wheelhouse 包含空 wheel")
        seen: set[str] = set()
        expanded = 0
        for info in infos:
            raw_name = info.filename[:-1] if info.is_dir() else info.filename
            normalized = normalize_payload_path(raw_name)
            expected = f"{normalized}/" if info.is_dir() else normalized
            if info.filename != expected or normalized.casefold() in seen:
                raise UpdatePackageError("离线 wheel 包含危险或重复路径")
            seen.add(normalized.casefold())
            if info.flag_bits & 0x1 or info.compress_type not in ALLOWED_COMPRESSION:
                raise UpdatePackageError("离线 wheel 使用不安全的 ZIP 特性")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(unix_mode)
            allowed_types = {0, stat.S_IFDIR} if info.is_dir() else {0, stat.S_IFREG}
            if file_type not in allowed_types:
                raise UpdatePackageError("离线 wheel 包含符号链接或特殊文件")
            expanded += info.file_size
            if info.compress_size == 0 and info.file_size > 0:
                raise UpdatePackageError("离线 wheel 包含异常压缩条目")
            if info.compress_size and info.file_size / info.compress_size > 200:
                raise UpdatePackageError("离线 wheel 包含异常压缩比条目")
        return len(infos), expanded


def normalize_payload_path(value: object) -> str:
    if not isinstance(value, str):
        raise UpdatePackageError("更新包文件路径必须是字符串")
    raw = value
    if (
        not raw or raw != raw.strip() or len(raw) > 500
        or "\x00" in raw or "\\" in raw
    ):
        raise UpdatePackageError("更新包包含非法文件路径")
    windows = PureWindowsPath(raw)
    posix = PurePosixPath(raw)
    if windows.is_absolute() or windows.drive or posix.is_absolute():
        raise UpdatePackageError("更新包不能包含绝对路径、盘符或 UNC 路径")
    if any(part in {"", ".", ".."} for part in posix.parts):
        raise UpdatePackageError("更新包文件路径不能包含 . 或 ..")
    for part in posix.parts:
        if ":" in part:
            raise UpdatePackageError("更新包文件路径不能包含盘符或数据流")
        if part != part.rstrip(" ."):
            raise UpdatePackageError("更新包路径组件不能以空格或点结尾")
        if part.split(".", 1)[0].upper() in WINDOWS_RESERVED:
            raise UpdatePackageError("更新包路径包含 Windows 保留设备名")
    return "/".join(posix.parts)


def _load_public_key(path: Path) -> Ed25519PublicKey:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UpdatePackageError("更新包签名公钥不存在") from exc
    try:
        key = serialization.load_pem_public_key(raw)
    except ValueError:
        try:
            decoded = base64.b64decode(raw.strip(), validate=True)
            key = Ed25519PublicKey.from_public_bytes(decoded)
        except (ValueError, TypeError) as exc:
            raise UpdatePackageError("更新包签名公钥格式无效") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise UpdatePackageError("更新包签名公钥不是 Ed25519")
    return key


def _read_json_entry(archive: zipfile.ZipFile, name: str, limit: int) -> dict:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise UpdatePackageError(f"更新包缺少 {name}") from exc
    if info.file_size > limit:
        raise UpdatePackageError(f"{name} 超过允许大小")
    try:
        value = json.loads(archive.read(info).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdatePackageError(f"{name} 不是有效 UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise UpdatePackageError(f"{name} 必须是 JSON 对象")
    return value


def _validate_manifest(manifest: dict) -> tuple[list[dict], dict]:
    allowed = {
        "schema_version", "component", "version", "release_id", "created_at",
        "key_id", "target", "min_updater_version", "entrypoint", "files",
    }
    if set(manifest) != allowed:
        raise UpdatePackageError("更新清单字段不完整或包含未知字段")
    if manifest.get("schema_version") != 1 or manifest.get("component") != "agent":
        raise UpdatePackageError("只接受 schema version 1 的 Agent 更新包")
    version = manifest.get("version")
    min_updater = manifest.get("min_updater_version")
    release_id = manifest.get("release_id")
    key_id = manifest.get("key_id")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise UpdatePackageError("Agent 版本必须是语义化版本")
    if not isinstance(min_updater, str) or not VERSION_RE.fullmatch(min_updater):
        raise UpdatePackageError("最低更新助手版本格式无效")
    if not isinstance(release_id, str) or not RELEASE_ID_RE.fullmatch(release_id):
        raise UpdatePackageError("release ID 格式无效")
    if not isinstance(key_id, str) or not KEY_ID_RE.fullmatch(key_id):
        raise UpdatePackageError("签名 key ID 格式无效")
    created_at = manifest.get("created_at")
    if not isinstance(created_at, str) or len(created_at) > 64:
        raise UpdatePackageError("创建时间格式无效")

    target = manifest.get("target")
    if not isinstance(target, dict) or set(target) != {"os", "arch", "python_abi"}:
        raise UpdatePackageError("更新清单缺少精确目标环境")
    if target.get("os") not in PLATFORM_VALUES:
        raise UpdatePackageError("目标操作系统必须是 windows 或 linux")
    if target.get("arch") not in ARCH_VALUES:
        raise UpdatePackageError("目标 CPU 架构不受支持")
    python_abi = target.get("python_abi")
    if not isinstance(python_abi, str) or not PYTHON_ABI_RE.fullmatch(python_abi):
        raise UpdatePackageError("目标 Python ABI 格式无效")

    entrypoint = normalize_payload_path(manifest.get("entrypoint"))
    if not entrypoint.endswith(".py"):
        raise UpdatePackageError("Agent 入口点必须是 Python 源文件")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise UpdatePackageError("更新清单必须包含至少一个文件")
    normalized_files: list[dict] = []
    seen: set[str] = set()
    seen_casefold: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise UpdatePackageError("更新清单文件记录格式无效")
        path = normalize_payload_path(item.get("path"))
        if path in seen or path.casefold() in seen_casefold:
            raise UpdatePackageError("更新清单包含重复文件")
        seen.add(path)
        seen_casefold.add(path.casefold())
        size = item.get("size")
        digest = item.get("sha256")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise UpdatePackageError("更新清单文件大小无效")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise UpdatePackageError("更新清单文件 SHA-256 无效")
        normalized_files.append({"path": path, "size": size, "sha256": digest})
    if entrypoint not in seen:
        raise UpdatePackageError("Agent 入口点未包含在文件清单中")
    if "requirements.lock" not in seen:
        raise UpdatePackageError("更新包缺少离线依赖锁文件")
    wheel_paths = [path for path in seen if path.startswith("wheelhouse/")]
    if any(not path.lower().endswith(".whl") for path in wheel_paths):
        raise UpdatePackageError("离线 wheelhouse 只能包含 wheel 文件")
    return normalized_files, target


def verify_update_package(
    path: Path,
    trusted_keys_dir: Path,
    *,
    max_package_bytes: int,
    max_expanded_bytes: int,
    max_entries: int,
) -> VerifiedUpdatePackage:
    try:
        package_info = path.lstat()
    except OSError as exc:
        raise UpdatePackageError("更新包不存在") from exc
    if not stat.S_ISREG(package_info.st_mode) or path.is_symlink():
        raise UpdatePackageError("更新包必须是普通文件")
    package_size = package_info.st_size
    if package_size <= 0 or package_size > max_package_bytes:
        raise UpdatePackageError("更新包为空或超过允许大小")
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdatePackageError("更新包不是有效 ZIP 文件") from exc

    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > max_entries:
            raise UpdatePackageError("更新包文件数量为空或超过限制")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or len(names) != len({name.casefold() for name in names}):
            raise UpdatePackageError("更新包包含重复 ZIP 路径")
        expanded_size = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise UpdatePackageError("不接受加密 ZIP 条目")
            if info.is_dir():
                raise UpdatePackageError("更新包不能包含目录条目")
            if info.compress_type not in ALLOWED_COMPRESSION:
                raise UpdatePackageError("更新包使用了不支持的压缩算法")
            normalized = normalize_payload_path(info.filename)
            if normalized != info.filename:
                raise UpdatePackageError("ZIP 路径必须使用规范的正斜杠形式")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(unix_mode)
            if file_type not in {0, stat.S_IFREG}:
                raise UpdatePackageError("更新包不能包含符号链接或特殊文件")
            expanded_size += info.file_size
            if expanded_size > max_expanded_bytes:
                raise UpdatePackageError("更新包展开后超过允许大小")
            if info.compress_size == 0 and info.file_size > 0:
                raise UpdatePackageError("更新包包含异常压缩条目")
            if info.compress_size and info.file_size / info.compress_size > 200:
                raise UpdatePackageError("更新包包含异常压缩比条目")

        manifest = _read_json_entry(archive, "manifest.json", 1024 * 1024)
        files, target = _validate_manifest(manifest)
        key_id = str(manifest["key_id"])
        expected_names = {
            "manifest.json", "manifest.sig",
            *(f"payload/{item['path']}" for item in files),
        }
        if set(names) != expected_names:
            raise UpdatePackageError("更新包包含未登记文件或缺少清单文件")
        signature_info = archive.getinfo("manifest.sig")
        if signature_info.file_size > 1024:
            raise UpdatePackageError("manifest.sig 超过允许大小")
        try:
            encoded_signature = archive.read(signature_info)
            signature = base64.b64decode(encoded_signature.strip(), validate=True)
        except (KeyError, ValueError) as exc:
            raise UpdatePackageError("manifest.sig 格式无效") from exc
        if len(signature) != 64:
            raise UpdatePackageError("manifest.sig 长度无效")
        public_key = _load_public_key(trusted_keys_dir / f"{key_id}.pub")
        canonical = canonical_manifest(manifest)
        try:
            public_key.verify(signature, canonical)
        except InvalidSignature as exc:
            raise UpdatePackageError("更新包 Ed25519 签名验证失败") from exc

        for item in files:
            info = archive.getinfo(f"payload/{item['path']}")
            if info.file_size != item["size"]:
                raise UpdatePackageError(f"文件大小与清单不一致：{item['path']}")
            digest = hashlib.sha256()
            with archive.open(info) as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
            if digest.hexdigest() != item["sha256"]:
                raise UpdatePackageError(f"文件哈希与清单不一致：{item['path']}")

        lock_info = archive.getinfo("payload/requirements.lock")
        if lock_info.file_size > 2 * 1024 * 1024:
            raise UpdatePackageError("requirements.lock 超过允许大小")
        has_dependencies = validate_requirements_lock(archive.read(lock_info))
        wheel_paths = [
            item["path"] for item in files
            if item["path"].startswith("wheelhouse/")
        ]
        if has_dependencies and not wheel_paths:
            raise UpdatePackageError("锁定依赖必须提供离线 wheelhouse")
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
                raise UpdatePackageError("wheel 内部文件数量超过限制")
            if expanded_size + nested_expanded > max_expanded_bytes:
                raise UpdatePackageError("wheel 展开后超过更新包大小限制")
        expanded_size += nested_expanded

    manifest_json = canonical_manifest(manifest).decode("utf-8")
    return VerifiedUpdatePackage(
        manifest=manifest,
        manifest_json=manifest_json,
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
    )
