"""B8 数据互操作与导出：全量打包（库快照/简历产物/素材库/用量/周报），零密钥断言。

导出物只含运行数据，绝不含 config.yaml（API Key 的唯一存放处）。
"""

from __future__ import annotations

import sqlite3
import zipfile
from io import BytesIO
from pathlib import Path

# 允许进入导出包的数据目录成员（白名单制，防误打包敏感文件）
_ALLOWED_DATA_ENTRIES = (
    "resumes",
    "resume_materials.xlsx",
    "resume_materials.index.json",
    "usage.jsonl",
    "weekly_reports",
)


def build_export_archive(base_dir: Path, *, include_resumes: bool = True) -> bytes:
    """生成全量导出 zip 字节流。库快照用 SQLite 在线备份 API，可在面板运行时导出。"""
    db_path = base_dir / "data" / "openjob.db"
    if not db_path.exists():
        raise FileNotFoundError(f"数据库不存在：{db_path}")

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # 1) 数据库快照（在线备份到同目录临时文件再落 zip，兼容 WAL）
        snapshot_tmp = db_path.with_suffix(".db.export-snapshot")
        src = sqlite3.connect(str(db_path))
        try:
            dest = sqlite3.connect(str(snapshot_tmp))
            try:
                src.backup(dest)
            finally:
                dest.close()
        finally:
            src.close()
        try:
            archive.writestr("openjob.db", snapshot_tmp.read_bytes())
        finally:
            snapshot_tmp.unlink(missing_ok=True)

        # 2) 数据目录白名单成员
        data_dir = base_dir / "data"
        for entry in _ALLOWED_DATA_ENTRIES:
            if not include_resumes and entry == "resumes":
                continue
            entry_path = data_dir / entry
            if not entry_path.exists():
                continue
            if entry_path.is_file():
                archive.write(entry_path, f"data/{entry}")
            else:
                for file_path in sorted(entry_path.rglob("*")):
                    if file_path.is_file():
                        archive.write(file_path, f"data/{entry}/{file_path.relative_to(entry_path).as_posix()}")

        # 3) 结构化清单（便于新目录恢复时校验）
        archive.writestr(
            "EXPORT-MANIFEST.txt",
            "OpenJob 全量数据导出\n"
            "内容：openjob.db 快照 + data/ 白名单成员（简历产物/素材库/用量/周报）\n"
            "恢复：解压后把 openjob.db 放回 data/，其余按目录对应放置\n"
            "注意：本包不含 config.yaml（API Key 只存在本地 config.yaml，请自行安全保管）\n"
            f"导出时间：{__import__('datetime').datetime.now().isoformat(timespec='seconds')}\n",
        )
    return buffer.getvalue()


def assert_export_has_no_secrets(archive_bytes: bytes, config_text: str) -> None:
    """验收断言：导出包不含 config.yaml，也不含其中的任何非平凡行（Key/姓名行等）。"""
    archive = zipfile.ZipFile(BytesIO(archive_bytes))
    names = archive.namelist()
    if "config.yaml" in names:
        raise AssertionError("导出包含 config.yaml")
    secret_lines = [
        line.strip()
        for line in config_text.splitlines()
        if line.strip() and "keywords" not in line and ":" in line
    ]
    joined = "\n".join(
        archive.read(name).decode("utf-8", errors="replace") for name in names
    )
    for line in secret_lines:
        if len(line) >= 8 and line in joined:
            raise AssertionError(f"导出包疑似包含配置内容：{line[:40]}")
