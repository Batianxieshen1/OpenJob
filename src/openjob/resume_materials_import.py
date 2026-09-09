"""智能导入层：扫描任意 Excel → 字段映射 → 预览 → 用户确认 → 严格转换。

两层解析架构：本模块负责扫描/映射/归一化/行级验证/资源上限/会话管理；
最终严格校验复用 resume_materials.parse_workbook()。source/notes 绝不进入候选 prompt。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import uuid as _uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook

from openjob.resume_materials import (
    DEFAULT_HEADERS,
    EXAMPLE_ROW_MARKERS,
    MATERIAL_TYPES,
    MAX_XLSX_BYTES,
    SHEET_NAME,
    MaterialLibrary,
    MaterialLibraryError,
    parse_workbook,
    save_library_atomically,
    split_cell_list,
)

# ---------- 资源上限 ----------
MAX_IMPORT_SHEETS = 50
MAX_IMPORT_ROWS_PER_SHEET = 5_000
MAX_IMPORT_TOTAL_ROWS = 20_000
MAX_IMPORT_COLUMNS = 100
MAX_XLSX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
IMPORT_SESSION_TTL_SECONDS = 24 * 60 * 60

MAX_SELECTED_MATERIALS = 4
MAX_MATERIAL_BACKED_CHANGES = 3

IMPORT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

FIELD_ALIASES: dict[str, list[str]] = {
    "id": ["id", "编号", "序号", "素材id", "唯一标识"],
    "type": ["type", "类别", "类型", "分类"],
    "title": [
        "title", "名称", "名称/标题", "名称 标题", "项目名称", "经历名称", "奖项名称",
        "证书名称", "标题", "岗位名称", "奖项", "证书", "项目", "名称标题", "奖项全称",
    ],
    "organization": [
        "organization", "公司", "单位", "学校", "组织/机构", "组织 机构", "颁发机构",
        "主办方", "机构",
    ],
    "role": ["role", "角色", "职位", "职务", "担任身份", "角色/职位", "角色 职位", "负责方向"],
    "start_date": ["start_date", "开始时间", "起始时间", "入职时间", "开始", "起始"],
    "end_date": ["end_date", "结束时间", "离职时间", "有效期至", "获奖时间", "结束"],
    "description": [
        "description", "描述", "详细描述", "工作内容", "职责", "项目内容", "star",
        "详细描述（star 法则", "详细描述(star 法则", "做了什么", "内容", "说明",
    ],
    "achievements": [
        "achievements", "成果", "量化成果", "结果", "业绩", "排名", "获奖等级",
        "成果/影响", "分数/等级", "分数 等级",
    ],
    "skills": ["skills", "技能", "工具", "技术栈", "关键技能", "方法", "技能特长"],
    "keywords": ["keywords", "关键词", "标签", "tags"],
    "target_directions": [
        "target_directions", "适用岗位", "投递方向", "求职方向", "岗位类型", "目标方向", "方向",
    ],
    "source": ["source", "证明材料", "证明材料路径", "链接", "核验来源", "来源"],
    "resume_allowed": ["resume_allowed", "是否写入简历", "可用于简历", "简历状态"],
    "priority": ["priority", "重要程度", "优先级"],
    "notes": ["notes", "备注", "私密备注"],
}

IGNORE_FIELD = "__ignore__"

SHEET_TYPE_KEYWORDS = [
    ("project", ("项目",)),
    ("experience", ("经历", "实习")),
    ("award", ("奖项", "竞赛", "获奖")),
    ("student_work", ("学生工作", "社团", "班委", "学生会")),
    ("campus_activity", ("校园活动", "志愿", "活动")),
    ("certification", ("证书", "证明")),
    ("skill_evidence", ("技能",)),
]

CATEGORY_TYPE_MAP = {
    "实习经历": "experience", "实习": "experience", "项目": "project", "项目经历": "project",
    "奖项": "award", "竞赛": "award", "获奖": "award", "校园实践": "campus_activity",
    "校园活动": "campus_activity", "志愿": "campus_activity", "学生工作": "student_work",
    "社团": "student_work", "学生会": "student_work", "技能": "skill_evidence",
    "证书": "certification", "语言": "certification",
}


# ---------- 资源上限 ----------

def _check_zip_expansion(content: bytes) -> None:
    """检查 ZIP entry 声明的总解压尺寸；超限在解析前拒绝（防 zip 炸弹）。"""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        total = sum(entry.file_size for entry in archive.infolist())
    if total > MAX_XLSX_UNCOMPRESSED_BYTES:
        raise MaterialLibraryError(
            f"XLSX 解压后大小 {total // (1024 * 1024)}MB 超过上限 "
            f"{MAX_XLSX_UNCOMPRESSED_BYTES // (1024 * 1024)}MB"
        )


def _check_row_limit(count: int, *, sheet_name: str = "") -> None:
    prefix = f"Sheet「{sheet_name}」" if sheet_name else ""
    if count > MAX_IMPORT_ROWS_PER_SHEET:
        raise MaterialLibraryError(
            f"{prefix}数据行数 {count} 超过单表上限 {MAX_IMPORT_ROWS_PER_SHEET}"
        )


def _check_total_limit(count: int) -> None:
    if count > MAX_IMPORT_TOTAL_ROWS:
        raise MaterialLibraryError(
            f"总数据行数 {count} 超过总上限 {MAX_IMPORT_TOTAL_ROWS}"
        )


def _check_column_limit(count: int, *, sheet_name: str = "") -> None:
    prefix = f"Sheet「{sheet_name}」" if sheet_name else ""
    if count > MAX_IMPORT_COLUMNS:
        raise MaterialLibraryError(
            f"{prefix}列数 {count} 超过上限 {MAX_IMPORT_COLUMNS}"
        )


def _check_sheet_count(count: int) -> None:
    if count > MAX_IMPORT_SHEETS:
        raise MaterialLibraryError(f"Sheet 数量 {count} 超过上限 {MAX_IMPORT_SHEETS}")


# ---------- 会话模型 ----------

@dataclass
class ImportSession:
    import_id: str
    filename: str
    source_sha256: str
    staged_path: Path | None
    created_at: str
    sheets: list[dict]
    warnings: list[str]

    def to_public_dict(self) -> dict:
        """浏览器响应：不含服务器绝对路径。"""
        return {
            "import_id": self.import_id,
            "filename": self.filename,
            "source_sha256": self.source_sha256,
            "created_at": self.created_at,
            "sheets": self.sheets,
            "warnings": self.warnings,
        }

    def to_storage_dict(self) -> dict:
        """analysis.json 存储：含暂存路径（仅服务端使用）。"""
        payload = self.to_public_dict()
        payload["staged_path"] = str(self.staged_path) if self.staged_path else ""
        return payload

    @staticmethod
    def from_storage_dict(data: dict) -> ImportSession:
        staged = data.get("staged_path")
        return ImportSession(
            import_id=str(data.get("import_id") or ""),
            filename=str(data.get("filename") or ""),
            source_sha256=str(data.get("source_sha256") or ""),
            created_at=str(data.get("created_at") or ""),
            staged_path=Path(staged) if staged else None,
            sheets=list(data.get("sheets") or []),
            warnings=list(data.get("warnings") or []),
        )


def cleanup_expired_import_sessions(imports_dir: Path, *, now: datetime | None = None) -> int:
    """清理超过 TTL 的暂存会话目录；只处理 imports 根内合法命名的子目录。"""
    root = imports_dir.resolve()
    if not root.exists():
        return 0

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    removed = 0
    for child in root.iterdir():
        if not child.is_dir() or not IMPORT_ID_PATTERN.fullmatch(child.name):
            continue
        resolved = child.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue

        analysis_path = resolved / "analysis.json"
        created_at: datetime | None = None
        try:
            data = json.loads(analysis_path.read_text(encoding="utf-8"))
            raw_created_at = str(data.get("created_at") or "")
            if raw_created_at:
                created_at = datetime.fromisoformat(raw_created_at.replace("Z", "+00:00"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            created_at = None

        if created_at is None:
            timestamp_source = analysis_path if analysis_path.exists() else resolved
            created_at = datetime.fromtimestamp(timestamp_source.stat().st_mtime, timezone.utc)
        elif created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        if (current - created_at.astimezone(timezone.utc)).total_seconds() < IMPORT_SESSION_TTL_SECONDS:
            continue

        shutil.rmtree(resolved)
        removed += 1
    return removed


# ---------- 表头/映射/类型推断 ----------

def _norm_header(value) -> str:
    s = str(value or "").strip().lower()
    s = re.sub(r"[\s\u3000]+", "", s)
    return s.replace("（", "(").replace("）", ")")


def _alias_score(header: str, aliases: list[str]) -> int:
    h = _norm_header(header)
    best = 0
    for alias in aliases:
        a = _norm_header(alias)
        if not a:
            continue
        if h == a:
            best = max(best, 3)
        elif a in h or h in a:
            best = max(best, 2)
    return best


def infer_header_row(rows: list[list[object]]) -> int | None:
    """扫描前 30 行，按别名命中数+非空格数推断表头行；不可靠时返回 None。"""
    all_aliases = [a for aliases in FIELD_ALIASES.values() for a in aliases]
    best_row, best_score = None, 0
    for idx, row in enumerate(rows[:30]):
        cells = [str(c).strip() for c in (row or []) if c is not None and str(c).strip()]
        if not cells:
            continue
        alias_hits = sum(1 for c in cells if _alias_score(c, all_aliases) >= 3)
        if len(cells) == 1:
            # 单列表头：必须精确命中某字段别名（如“项目名称”）
            if alias_hits >= 1 and best_score < 4:
                best_row, best_score = idx, 4
            continue
        score = alias_hits * 2 + min(len(cells), 8)
        if alias_hits >= 2 and score > best_score:
            best_row, best_score = idx, score
    return best_row


def infer_column_mapping(headers: list[str], sample_rows: list[list[object]], *, sheet_name: str = ""):
    """推断每个原始列 → 标准字段；输出映射列表（含置信度/原因/样例值）。"""
    taken: set[str] = set()
    mapping = []
    for col_idx, header in enumerate(headers):
        samples = [
            str(r[col_idx]).strip()
            for r in sample_rows
            if len(r) > col_idx and r[col_idx] is not None and str(r[col_idx]).strip()
        ][:3]
        best_field, best_score, reason = IGNORE_FIELD, 0, "未命中别名"
        for field_name, aliases in FIELD_ALIASES.items():
            if field_name in taken:
                continue
            score = _alias_score(header, aliases)
            if score > best_score:
                best_field, best_score = field_name, score
                reason = "命中中文别名" if _norm_header(header) in [_norm_header(a) for a in aliases] else "别名包含匹配"
        if best_field == IGNORE_FIELD and samples:
            date_like = sum(
                1 for s in samples
                if re.match(r"^20\d{2}[.\-/年]\d{1,2}", s) or s == "至今"
            )
            if date_like >= max(1, len(samples) // 2):
                if any(k in _norm_header(header) for k in ("开始", "起始", "入职", "获取")):
                    best_field, best_score, reason = "start_date", 1, "样例值形如日期"
                elif any(k in _norm_header(header) for k in ("结束", "离职", "有效")):
                    best_field, best_score, reason = "end_date", 1, "样例值形如日期"
        if best_field != IGNORE_FIELD:
            taken.add(best_field)
        mapping.append({
            "source_column": str(header),
            "target_field": best_field,
            "confidence": round(min(best_score / 3, 1), 2),
            "reason": reason,
            "sample_values": samples,
        })
    return mapping


def _infer_sheet_type(sheet_name: str, mapping: list[dict]):
    for field_name, keywords in SHEET_TYPE_KEYWORDS:
        if any(k in sheet_name for k in keywords):
            return field_name, "Sheet 名称"
    type_col = next((m for m in mapping if m["target_field"] == "type"), None)
    if type_col:
        for sample in type_col.get("sample_values", []):
            for value, t in CATEGORY_TYPE_MAP.items():
                if value in sample:
                    return t, "类别列"
    title_col = next((m for m in mapping if m["target_field"] == "title"), None)
    if title_col:
        for sample in title_col.get("sample_values", []):
            for value, t in CATEGORY_TYPE_MAP.items():
                if value in sample:
                    return t, "标题关键词"
    return "other", "低置信度"


def _norm_date_value(value):
    """日期单元格/字符串 → YYYY、YYYY-MM 或原值（无法解析时保留原值并警告）。"""
    if value is None:
        return ""
    if hasattr(value, "year"):
        if value.month == 1 and value.day == 1:
            return str(value.year)
        return f"{value.year}-{str(value.month).zfill(2)}"
    raw = str(value).strip()
    if not raw or raw == "至今":
        return raw
    m = re.match(r"^(20\d{2})[.\-/年](\d{1,2})[月]?$", raw)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}"
    if re.match(r"^20\d{2}$", raw):
        return raw
    return raw


def _norm_header_raw(value) -> str:
    return str(value).strip() if value is not None else ""


def _is_example_row(row_values: dict) -> bool:
    joined = " ".join(str(v) for v in row_values.values())
    return (
        any(marker in joined for marker in EXAMPLE_ROW_MARKERS)
        or "请替换" in joined
        or "请改成自己的" in joined
    )


def _map_row_values(headers: list[str], mapping: list[dict], row) -> dict:
    mapped: dict[str, list[str]] = {}
    for m in mapping:
        target = m["target_field"]
        if target == IGNORE_FIELD:
            continue
        try:
            idx = headers.index(m["source_column"])
        except ValueError:
            continue
        raw = row[idx] if idx < len(row) else None
        text = str(raw).strip() if raw is not None else ""
        if text:
            mapped.setdefault(target, []).append(text)
    return {k: "；".join(v) for k, v in mapped.items()}


# ---------- 统一 Sheet 读取与行级验证（preview 与 confirm 共用） ----------

@dataclass(frozen=True)
class ValidatedSheetConfig:
    name: str
    include: bool
    header_row: int  # 1-based Excel 行号
    field_mapping: dict[str, str]  # 源列名 → 标准字段
    type_override: str | None
    excluded_rows: frozenset[int]  # 真实 Excel 行号


@dataclass(frozen=True)
class ImportRowValidation:
    excel_row: int
    generated_id: str
    material: dict
    status: str  # valid / invalid / example
    issues: tuple[str, ...]
    excluded: bool


@dataclass(frozen=True)
class SheetValidationResult:
    name: str
    columns: tuple[str, ...]
    mapping: tuple[dict, ...]
    rows: tuple[ImportRowValidation, ...]

    @property
    def valid_rows(self) -> tuple[ImportRowValidation, ...]:
        return tuple(row for row in self.rows if row.status == "valid" and not row.excluded)

    @property
    def invalid_rows(self) -> tuple[ImportRowValidation, ...]:
        return tuple(row for row in self.rows if row.status == "invalid" and not row.excluded)


def _read_workbook_rows(
    content: bytes,
    sheet_name: str,
    *,
    header_row: int | None = None,
) -> list[tuple[int, list]]:
    """读取指定 Sheet 全部物理行，返回 (Excel 行号, 行值) 列表；强制资源上限。"""
    wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    try:
        if sheet_name not in wb.sheetnames:
            raise MaterialLibraryError(f"Sheet「{sheet_name}」不存在")
        sheet = wb[sheet_name]
        result: list[tuple[int, list]] = []
        max_columns = 0
        data_rows = 0
        for excel_row, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            cells = list(row)
            max_columns = max(max_columns, len(cells))
            if header_row is not None and excel_row > header_row and any(
                cell is not None and str(cell).strip() for cell in cells
            ):
                data_rows += 1
                _check_row_limit(data_rows, sheet_name=sheet_name)
            result.append((excel_row, cells))
        _check_column_limit(max_columns, sheet_name=sheet_name)
        return result
    finally:
        wb.close()


def validate_sheet_config(
    session: ImportSession,
    sheet_config: dict,
    defaults: dict,
) -> ValidatedSheetConfig:
    """校验单个 Sheet 的确认配置：schema/白名单/引用完整性，错误抛 ValueError。"""
    name = str(sheet_config.get("name") or "")
    analysis_sheet = next((s for s in session.sheets if s.get("name") == name), None)
    if analysis_sheet is None:
        raise ValueError(f"Sheet「{name}」不在分析会话中")

    include = bool(sheet_config.get("include", True))

    header_row_raw = sheet_config.get("header_row")
    if header_row_raw is None:
        header_row_raw = analysis_sheet.get("header_row")
    try:
        header_row = int(header_row_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Sheet「{name}」表头行必须是正整数") from exc
    if header_row < 1 or header_row > MAX_IMPORT_ROWS_PER_SHEET + 1:
        raise ValueError(f"Sheet「{name}」表头行 {header_row} 超出范围")

    mapping_raw = sheet_config.get("field_mapping")
    if not isinstance(mapping_raw, dict):
        raise ValueError(f"Sheet「{name}」field_mapping 必须是对象")
    field_mapping: dict[str, str] = {}
    for source_column, target in mapping_raw.items():
        target = str(target or "").strip()
        if target == IGNORE_FIELD or not target:
            continue
        if target not in DEFAULT_HEADERS:
            raise ValueError(f"Sheet「{name}」：列「{source_column}」映射了未知目标字段「{target}」")
        field_mapping[str(source_column)] = target
    # 每个标准字段只允许映射一次（description 等多列拼接在归一化时按同名目标合并）
    targets = [t for t in field_mapping.values() if t in ("id", "type", "resume_allowed", "priority")]
    if len(targets) != len(set(targets)):
        raise ValueError(f"Sheet「{name}」：id/type/resume_allowed/priority 各只允许映射一列")

    type_override_raw = sheet_config.get("type_override")
    if type_override_raw in (None, ""):
        type_override = None
    else:
        type_override = str(type_override_raw).strip()
        if type_override not in MATERIAL_TYPES:
            raise ValueError(f"Sheet「{name}」：type_override「{type_override}」不是合法素材类型")

    excluded_rows = frozenset()
    excluded_raw = sheet_config.get("excluded_rows") or []
    if not isinstance(excluded_raw, list):
        raise ValueError(f"Sheet「{name}」excluded_rows 必须是数组")
    excluded_values = []
    for raw in excluded_raw:
        try:
            excluded_values.append(int(raw))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Sheet「{name}」excluded_rows 含非整数行号：{raw}") from exc
    if excluded_values:
        excluded_rows = frozenset(excluded_values)

    allowed_raw = (defaults or {}).get("resume_allowed", True)
    if not isinstance(allowed_raw, bool):
        raise ValueError("defaults.resume_allowed 必须是布尔值")
    priority_raw = (defaults or {}).get("priority", 3)
    if not isinstance(priority_raw, int) or not 1 <= priority_raw <= 5:
        raise ValueError("defaults.priority 必须是 1-5 的整数")

    return ValidatedSheetConfig(
        name=name,
        include=include,
        header_row=header_row,
        field_mapping=field_mapping,
        type_override=type_override,
        excluded_rows=excluded_rows,
    )


def read_configured_sheet_rows(source_content: bytes, *, sheet_name: str, header_row: int):
    """读取指定 Sheet 全部物理行（含真实 Excel 行号），强制资源上限。"""
    return _read_workbook_rows(source_content, sheet_name, header_row=header_row)


def normalize_and_validate_sheet_rows(
    sheet_rows: list[tuple[int, list]],
    *,
    config: ValidatedSheetConfig,
    defaults: dict,
    inferred_type: str = "other",
) -> SheetValidationResult:
    """映射 + 类型归一 + 稳定 ID + 示例检测 + 行级预校验（preview/confirm 共用）。"""
    header_excel_row = config.header_row
    header_values = next((row for excel_row, row in sheet_rows if excel_row == header_excel_row), None)
    if header_values is None:
        raise ValueError(f"Sheet「{config.name}」不存在第 {header_excel_row} 行表头")
    headers = [_norm_header_raw(c) for c in header_values]
    if not any(headers):
        raise ValueError(f"Sheet「{config.name}」第 {header_excel_row} 行表头为空")
    mapping = infer_column_mapping(headers, [], sheet_name=config.name) if not config.field_mapping else [
        {"source_column": col, "target_field": target, "confidence": 1.0, "reason": "用户确认"}
        for col, target in config.field_mapping.items()
    ]
    sheet_type_inferred = inferred_type
    if inferred_type == "other":
        sheet_type_inferred, _ = _infer_sheet_type(config.name, mapping)

    rows: list[ImportRowValidation] = []
    seen_ids: set[str] = set()
    slug = re.sub(r"[^a-z0-9]+", "", config.name.lower())[:12] or "sheet"
    default_priority = (defaults or {}).get("priority", 3)
    default_allowed = (defaults or {}).get("resume_allowed", True)

    data_ordinal = 0
    for excel_row, row in sheet_rows:
        if excel_row <= header_excel_row:
            continue
        if not any(c is not None and str(c).strip() for c in (row or [])):
            continue
        data_ordinal += 1
        mapped = _map_row_values(headers, mapping, row)
        status = "valid"
        issues: list[str] = []

        if _is_example_row(mapped):
            rows.append(ImportRowValidation(
                excel_row=excel_row, generated_id="", material={},
                status="example", issues=("模板示例行",),
                excluded=True,
            ))
            continue

        user_id = (mapped.get("id") or "").strip()
        if user_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", user_id):
            user_id = ""
            issues.append("id 非法，已改用系统生成 ID")
        if user_id and user_id.isdigit():
            user_id = f"{slug}_{user_id}"
        if not user_id:
            row_hash = hashlib.sha256(
                json.dumps(mapped, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()[:10]
            user_id = f"imp_{slug}_{row_hash}"

        if user_id in seen_ids:
            status = "invalid"
            issues.append(f"id「{user_id}」重复")
        seen_ids.add(user_id)

        raw_type = (mapped.get("type") or "").strip()
        row_type = next(
            (candidate for candidate in MATERIAL_TYPES if raw_type.lower() == candidate),
            next((t for key, t in CATEGORY_TYPE_MAP.items() if key in raw_type), ""),
        )
        if raw_type and not row_type:
            issues.append(f"类别「{raw_type[:40]}」无法识别，已使用 Sheet 类型推断")

        item = {
            "id": user_id,
            "type": config.type_override or row_type or sheet_type_inferred or "other",
            "title": (mapped.get("title") or "")[:120],
            "organization": (mapped.get("organization") or "")[:120],
            "role": (mapped.get("role") or "")[:80],
            "start_date": _norm_date_value(mapped.get("start_date")),
            "end_date": _norm_date_value(mapped.get("end_date")),
            "description": (mapped.get("description") or "")[:2000],
            "achievements": (mapped.get("achievements") or "")[:2000],
            "skills": split_cell_list(mapped.get("skills")),
            "keywords": split_cell_list(mapped.get("keywords")),
            "target_directions": split_cell_list(mapped.get("target_directions")),
            "source": (mapped.get("source") or "")[:500],
            "resume_allowed": default_allowed,
            "priority": default_priority,
            "notes": (mapped.get("notes") or "")[:500],
        }
        ra = (mapped.get("resume_allowed") or "").strip()
        if ra:
            lowered = ra.lower()
            if ra in {"是", "1"} or lowered == "true":
                item["resume_allowed"] = True
            elif ra in {"否", "0"} or lowered == "false" or "停" in ra:
                item["resume_allowed"] = False

        if not item["title"]:
            status = "invalid"
            issues.append("缺少标题")
        if not item["description"]:
            status = "invalid"
            issues.append("缺少事实描述")

        priority_raw = (mapped.get("priority") or "").strip()
        if priority_raw:
            try:
                priority = int(priority_raw)
                if not 1 <= priority <= 5:
                    raise ValueError
                item["priority"] = priority
            except ValueError:
                status = "invalid"
                issues.append("priority 必须是 1-5 的整数")

        excluded = excel_row in config.excluded_rows
        rows.append(ImportRowValidation(
            excel_row=excel_row,
            generated_id=user_id,
            material=item,
            status=status,
            issues=tuple(issues),
            excluded=excluded,
        ))

    return SheetValidationResult(
        name=config.name,
        columns=tuple(headers),
        mapping=tuple(mapping),
        rows=tuple(rows),
    )


def validate_import_sheet(
    source_content: bytes,
    *,
    session: ImportSession,
    sheet_config: dict,
    defaults: dict,
) -> SheetValidationResult:
    """读取一个 Sheet，返回 preview 与 confirm 共用的逐行验证结果。"""
    validated = validate_sheet_config(session, sheet_config, defaults)
    sheet_rows = read_configured_sheet_rows(
        source_content, sheet_name=validated.name, header_row=validated.header_row,
    )
    header_values = next((row for excel_row, row in sheet_rows if excel_row == validated.header_row), None)
    if header_values is None:
        raise ValueError(f"Sheet「{validated.name}」不存在第 {validated.header_row} 行表头")
    headers = [_norm_header_raw(cell) for cell in header_values]
    unknown_columns = sorted(set(validated.field_mapping) - set(headers))
    if unknown_columns:
        raise ValueError(
            f"Sheet「{validated.name}」第 {validated.header_row} 行不存在源列：{'、'.join(unknown_columns)}"
        )
    inferred, _ = _infer_sheet_type(
        validated.name,
        infer_column_mapping(headers, [row for excel_row, row in sheet_rows if excel_row > validated.header_row][:3], sheet_name=validated.name),
    )
    result = normalize_and_validate_sheet_rows(
        sheet_rows,
        config=validated,
        defaults=defaults,
        inferred_type=inferred,
    )
    actual_data_rows = {
        row.excel_row for row in result.rows if row.status != "example" or row.excel_row > validated.header_row
    }
    invalid_exclusions = sorted(validated.excluded_rows - actual_data_rows)
    if invalid_exclusions:
        raise ValueError(
            f"Sheet「{validated.name}」excluded_rows 包含不存在的数据行："
            f"{'、'.join(str(row) for row in invalid_exclusions)}"
        )
    return result


# ---------- 会话分析（扫描） ----------

def analyze_workbook(content: bytes, *, filename: str, imports_dir: Path | None = None) -> ImportSession:
    """扫描任意 Excel：定位表头、推断映射、生成预览与警告。不写正式素材库。"""
    if not filename.lower().endswith(".xlsx"):
        raise MaterialLibraryError("智能导入只支持 .xlsx 格式（.xls 请先转换为 .xlsx）")
    if len(content) > MAX_XLSX_BYTES:
        raise MaterialLibraryError(f"文件超过 {MAX_XLSX_BYTES // (1024 * 1024)}MB 上限")
    if not content.startswith(b"PK"):
        raise MaterialLibraryError("文件不是有效的 XLSX（ZIP）格式")
    _check_zip_expansion(content)

    wb_ro = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    _check_sheet_count(len(wb_ro.worksheets))

    sheets: list[dict] = []
    session_warnings: list[str] = []
    total_rows = 0
    for sheet in wb_ro.worksheets:
        hidden = sheet.sheet_state != "visible"
        rows = [list(r) for r in sheet.iter_rows(values_only=True)]
        if rows:
            _check_column_limit(len(rows[0]), sheet_name=sheet.title)
        if hidden:
            session_warnings.append(f"Sheet「{sheet.title}」为隐藏状态，默认不导入，可在确认页手动包含")
        header_row = infer_header_row(rows)
        info = {
            "name": sheet.title,
            "hidden": hidden,
            "header_row": (header_row + 1) if header_row is not None else None,
            "columns": [],
            "mapping": [],
            "preview": [],
            "row_count": 0,
            "valid_row_count": 0,
            "warning_count": 0,
        }
        if header_row is None:
            info["warning_count"] = 1
            info["warnings"] = ["无法可靠定位表头行，请在确认页手动指定表头行"]
            sheets.append(info)
            continue
        headers = [str(c).strip() if c is not None else "" for c in rows[header_row]]
        sample_rows = rows[header_row + 1 : header_row + 6]
        mapping = infer_column_mapping(headers, sample_rows, sheet_name=sheet.title)
        data_rows = [
            r for r in rows[header_row + 1 :]
            if any(c is not None and str(c).strip() for c in (r or []))
        ]
        _check_row_limit(len(data_rows), sheet_name=sheet.title)
        total_rows += len(data_rows)
        sheet_type, type_source = _infer_sheet_type(sheet.title, mapping)
        info["columns"] = headers
        info["mapping"] = mapping
        info["row_count"] = len(data_rows)
        info["inferred_type"] = sheet_type
        info["inferred_type_source"] = type_source
        preview = []
        for offset, row in enumerate(data_rows[:5]):
            mapped = _map_row_values(headers, mapping, row)
            preview.append({"row": offset + 1, "mapped": mapped})
        info["preview"] = preview
        info["valid_row_count"] = len(data_rows)
        sheets.append(info)

    wb_ro.close()
    _check_total_limit(total_rows)

    session = ImportSession(
        import_id=_uuid.uuid4().hex,
        filename=filename,
        source_sha256=hashlib.sha256(content).hexdigest(),
        staged_path=None,
        created_at=datetime.now(timezone.utc).isoformat(),
        sheets=sheets,
        warnings=session_warnings,
    )

    if imports_dir is not None:
        stage_dir = Path(imports_dir) / session.import_id
        stage_dir.mkdir(parents=True, exist_ok=True)
        staged = stage_dir / "source.xlsx"
        staged.write_bytes(content)
        session.staged_path = staged
        (stage_dir / "analysis.json").write_text(
            json.dumps(session.to_storage_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return session


# ---------- 归一化（基于行级验证结果的兼容包装） ----------

def normalize_import_rows(source_content: bytes, confirm: dict) -> tuple[list[dict], list[str]]:
    """按用户确认的映射归一化行（排除无效/示例/用户排除行），返回 (标准素材列表, 警告)。"""
    session = ImportSession.from_storage_dict(confirm.get("session") or {})
    defaults = confirm.get("defaults") or {}
    warnings: list[str] = []
    items: list[dict] = []
    for sheet_conf in confirm.get("sheets", []):
        if not sheet_conf.get("include", True):
            warnings.append(f"Sheet「{sheet_conf.get('name')}」已按你的选择跳过")
            continue
        result = validate_import_sheet(
            source_content, session=session, sheet_config=sheet_conf, defaults=defaults,
        )
        for row in result.rows:
            if row.status == "example":
                warnings.append(f"Sheet「{result.name}」第 {row.excel_row} 行为模板示例行，已忽略")
                continue
            if row.status == "invalid":
                warnings.append(
                    f"Sheet「{result.name}」第 {row.excel_row} 行已排除：{'；'.join(row.issues)}"
                )
                continue
            if row.excluded:
                warnings.append(f"Sheet「{result.name}」第 {row.excel_row} 行已按你的选择排除")
                continue
            items.append(row.material)
    return items, warnings


def commit_import(
    source_content: bytes,
    items: list[dict],
    *,
    xlsx_path: Path,
    index_path: Path,
) -> MaterialLibrary:
    """用户确认后的素材写成标准 XLSX，经 parse_workbook 严格终验后原子落盘。"""

    def _cell_value(value):
        if isinstance(value, list):
            return "、".join(str(v) for v in value)
        return value if value is not None else ""

    out = Workbook()
    sheet = out.active
    sheet.title = SHEET_NAME
    sheet.append(DEFAULT_HEADERS)
    for m in items:
        sheet.append([_cell_value(m.get(h, "")) for h in DEFAULT_HEADERS])
    buf = io.BytesIO()
    out.save(buf)
    library = parse_workbook(buf.getvalue(), filename="imported.xlsx")
    save_library_atomically(library, buf.getvalue(), xlsx_path=xlsx_path, index_path=index_path)
    return library
