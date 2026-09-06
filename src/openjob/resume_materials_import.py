"""智能导入层：扫描任意 Excel → 字段映射 → 预览 → 用户确认 → 严格转换。

两层解析架构：本模块只做扫描/映射/归一化；最终严格校验复用
resume_materials.parse_workbook()。source/notes 绝不进入候选 prompt。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook, load_workbook

from openjob.resume_materials import (
    EXAMPLE_ROW_MARKERS,
    DEFAULT_HEADERS,
    MAX_XLSX_BYTES,
    SHEET_NAME,
    MaterialLibraryError,
    parse_workbook,
    save_library_atomically,
    split_cell_list,
)

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


@dataclass
class ImportSession:
    import_id: str
    filename: str
    source_sha256: str
    staged_path: Path | None
    sheets: list[dict]
    warnings: list[str]

    def to_dict(self) -> dict:
        return {
            "import_id": self.import_id,
            "filename": self.filename,
            "source_sha256": self.source_sha256,
            "staged_path": str(self.staged_path) if self.staged_path else "",
            "sheets": self.sheets,
            "warnings": self.warnings,
        }

    @staticmethod
    def from_dict(data: dict) -> ImportSession:
        staged = data.get("staged_path")
        return ImportSession(
            import_id=str(data.get("import_id") or ""),
            filename=str(data.get("filename") or ""),
            source_sha256=str(data.get("source_sha256") or ""),
            staged_path=Path(staged) if staged else None,
            sheets=list(data.get("sheets") or []),
            warnings=list(data.get("warnings") or []),
        )


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
        if len(cells) < 2:
            continue
        alias_hits = sum(1 for c in cells if _alias_score(c, all_aliases) >= 3)
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
        for field, aliases in FIELD_ALIASES.items():
            if field in taken:
                continue
            score = _alias_score(header, aliases)
            if score > best_score:
                best_field, best_score = field, score
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
    for field, keywords in SHEET_TYPE_KEYWORDS:
        if any(k in sheet_name for k in keywords):
            return field, "Sheet 名称"
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
    """日期单元格/字符串 → YYYY、YYYY-MM 或原值（无法解析时保留原值）。"""
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


def analyze_workbook(content: bytes, *, filename: str, imports_dir: Path | None = None) -> ImportSession:
    """扫描任意 Excel：定位表头、推断映射、生成预览与警告。不写正式素材库。"""
    if not filename.lower().endswith(".xlsx"):
        raise MaterialLibraryError("智能导入只支持 .xlsx 格式")
    if len(content) > MAX_XLSX_BYTES:
        raise MaterialLibraryError("文件超过 10MB 上限")
    if not content.startswith(b"PK"):
        raise MaterialLibraryError("文件不是有效的 XLSX（ZIP）格式")

    wb_ro = load_workbook(io.BytesIO(content), data_only=True, read_only=True)

    sheets: list[dict] = []
    session_warnings: list[str] = []
    for sheet in wb_ro.worksheets:
        hidden = sheet.sheet_state != "visible"
        rows = [list(r) for r in sheet.iter_rows(values_only=True, max_row=200)]
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

    session = ImportSession(
        import_id=_uuid.uuid4().hex,
        filename=filename,
        source_sha256=hashlib.sha256(content).hexdigest(),
        staged_path=None,
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
            json.dumps(session.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return session


def normalize_import_rows(source_content: bytes, confirm: dict) -> tuple[list[dict], list[str]]:
    """按用户确认的映射归一化行，逐行预校验，返回 (标准素材列表, 警告)。

    确定无效的行（缺标题/缺描述/重复 id/示例行）会被排除并记录原因，不静默丢弃。
    最终严格校验由 commit 阶段的 parse_workbook 执行。
    """
    session = ImportSession.from_dict(confirm.get("session") or {})
    defaults = confirm.get("defaults") or {}
    default_allowed = bool(defaults.get("resume_allowed", True))
    try:
        default_priority = int(defaults.get("priority", 3))
    except (TypeError, ValueError):
        default_priority = 3
    default_priority = max(1, min(5, default_priority))

    wb = load_workbook(io.BytesIO(source_content), data_only=True, read_only=True)
    warnings: list[str] = []
    items: list[dict] = []
    seen_ids: set[str] = set()

    for sheet_conf in confirm.get("sheets", []):
        name = str(sheet_conf.get("name") or "")
        if not name:
            continue
        if not sheet_conf.get("include", True):
            warnings.append(f"Sheet「{name}」已按你的选择跳过")
            continue
        if name not in wb.sheetnames:
            warnings.append(f"Sheet「{name}」不存在，已跳过")
            continue
        sheet = wb[name]
        rows = [list(r) for r in sheet.iter_rows(values_only=True, max_row=200)]
        try:
            header_row = int(sheet_conf.get("header_row") or 1) - 1
        except (TypeError, ValueError):
            header_row = 0
        if header_row < 0 or header_row >= len(rows):
            warnings.append(f"Sheet「{name}」表头行无效，已跳过")
            continue
        headers = [str(c).strip() if c is not None else "" for c in rows[header_row]]
        field_mapping = dict(sheet_conf.get("field_mapping") or {})
        excluded = set()
        for raw in sheet_conf.get("excluded_rows") or []:
            try:
                excluded.add(int(raw))
            except (TypeError, ValueError):
                continue
        type_override = str(sheet_conf.get("type_override") or "").strip()
        inferred_type, _ = _infer_sheet_type(
            name,
            [{"target_field": target} for target in field_mapping.values()],
        )

        slug = re.sub(r"[^a-z0-9]+", "", name.lower())[:12] or "sheet"

        data_ordinal = 0
        for row in rows[header_row + 1 :]:
            if not any(c is not None and str(c).strip() for c in (row or [])):
                continue
            data_ordinal += 1
            if data_ordinal in excluded:
                warnings.append(f"Sheet「{name}」第 {data_ordinal} 行已按你的选择排除")
                continue
            mapped = _map_row_values(headers, list(_iter_mapping_rows(field_mapping)), row)
            if _is_example_row(mapped):
                warnings.append(f"Sheet「{name}」第 {data_ordinal} 行为模板示例行，已忽略")
                continue

            user_id = (mapped.get("id") or "").strip()
            if user_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", user_id):
                warnings.append(f"Sheet「{name}」第 {data_ordinal} 行 id 非法，已改用系统生成 ID")
                user_id = ""
            if user_id and user_id.isdigit():
                user_id = f"{slug}_{user_id}"
            if not user_id:
                row_hash = hashlib.sha256(
                    json.dumps(mapped, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()[:10]
                user_id = f"imp_{slug}_{row_hash}"

            item = {
                "id": user_id,
                "type": (
                    type_override
                    or next(
                        (t for k, t in CATEGORY_TYPE_MAP.items() if k in (mapped.get("type") or "")),
                        inferred_type or "other",
                    )
                ),
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
                "notes": "",
            }
            ra = (mapped.get("resume_allowed") or "").strip()
            if ra:
                lowered = ra.lower()
                if ra in {"是", "1"} or lowered == "true":
                    item["resume_allowed"] = True
                elif ra in {"否", "0"} or lowered == "false" or "停" in ra:
                    item["resume_allowed"] = False

            row_warnings = []
            if not item["title"]:
                row_warnings.append("缺少标题")
            if not item["description"]:
                row_warnings.append("缺少事实描述")
            if item["id"] in seen_ids:
                row_warnings.append(f"id「{item['id']}」与前面素材重复")
            if row_warnings:
                warnings.append(f"Sheet「{name}」第 {data_ordinal} 行已排除：{'；'.join(row_warnings)}")
                continue
            seen_ids.add(item["id"])
            items.append(item)

    wb.close()
    return items, warnings


def _iter_mapping_rows(field_mapping: dict):
    for source_column, target in field_mapping.items():
        yield {"source_column": source_column, "target_field": str(target or IGNORE_FIELD)}


def commit_import(
    source_content: bytes,
    items: list[dict],
    *,
    xlsx_path: Path,
    index_path: Path,
) -> MaterialLibrary:
    """用户确认后的素材写成标准 XLSX，经 parse_workbook 严格终验后原子落盘。"""
    out = Workbook()
    sheet = out.active
    sheet.title = SHEET_NAME
    sheet.append(DEFAULT_HEADERS)
    def _cell_value(value):
        if isinstance(value, list):
            return "、".join(str(v) for v in value)
        return value if value is not None else ""

    for m in items:
        sheet.append([_cell_value(m.get(h, "")) for h in DEFAULT_HEADERS])
    buf = io.BytesIO()
    out.save(buf)
    library = parse_workbook(buf.getvalue(), filename="imported.xlsx")
    save_library_atomically(library, buf.getvalue(), xlsx_path=xlsx_path, index_path=index_path)
    return library
