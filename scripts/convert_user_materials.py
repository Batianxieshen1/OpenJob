"""用户自维护素材表（多工作表中文格式）→ 标准素材库格式转换器。

用法：
    python scripts/convert_user_materials.py --input "源.xlsx" --output "目标.xlsx"
    python scripts/convert_user_materials.py --input 源.xlsx --output 目标.xlsx --dry-run
    python scripts/convert_user_materials.py --input 源.xlsx --output 目标.xlsx --force

规则：
- 源文件需包含 📋 经历 / 🏆 奖项 / 📜 证明 三张工作表；
- 不修改源文件；输出先写临时文件，成功后替换目标；
- 默认拒绝覆盖已存在的目标（--force 覆盖）；
- 缺少描述的事实素材会直接失败（可先用 --dry-run 查看统计）。
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

from openpyxl import Workbook, load_workbook

HEADERS = [
    "id", "type", "title", "organization", "role", "start_date", "end_date",
    "description", "achievements", "skills", "keywords", "target_directions",
    "source", "resume_allowed", "priority", "notes",
]

REQUIRED_SHEETS = ("📋 经历", "🏆 奖项", "📜 证明")


def cell(row, header_map, name):
    idx = header_map.get(name)
    if idx is None or idx >= len(row):
        return ""
    value = row[idx]
    return str(value).strip() if value is not None else ""


def norm_date(raw):
    """2025.04 / 2025.9 / 2025 → 2025-04 / 2025-09 / 2025；至今 原样。"""
    raw = (raw or "").strip()
    if not raw or raw == "至今":
        return raw
    parts = raw.replace("-", ".").split(".")
    if len(parts) == 1 and parts[0].isdigit():
        return parts[0]
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{parts[0]}-{parts[1].zfill(2)}"
    return raw


def stars_to_priority(raw):
    count = (raw or "").count("⭐")
    return max(1, min(5, count)) if count else 3


def classify_experience(category):
    if "实习" in category:
        return "experience"
    if "项目" in category:
        return "project"
    if "校园" in category or "实践" in category:
        return "campus_activity"
    if "技能" in category:
        return "skill_evidence"
    return "experience"


def parse_source(src: Path):
    """解析三张中文工作表，返回 (materials, warnings)。缺表/缺列/重复 id 直接失败。"""
    if not src.exists():
        raise SystemExit(f"失败：源文件不存在：{src}")
    wb = load_workbook(src, data_only=True, read_only=True)
    missing_sheets = [s for s in REQUIRED_SHEETS if s not in wb.sheetnames]
    if missing_sheets:
        raise SystemExit(f"失败：源文件缺少工作表：{'、'.join(missing_sheets)}")

    materials: list[dict] = []
    warnings: list[str] = []

    # ---------- 📋 经历 ----------
    sheet = wb["📋 经历"]
    rows = list(sheet.iter_rows(values_only=True))
    hm = {h.strip(): i for i, h in enumerate(rows[0]) if h}
    for col in ("名称 / 标题", "详细描述（STAR 法则", "关键技能", "适用岗位"):
        if col not in hm:
            raise SystemExit(f"失败：📋 经历表缺少列「{col}」")
    for row in rows[1:]:
        title = cell(row, hm, "名称 / 标题")
        if not title:
            continue
        seq = cell(row, hm, "序号") or len(materials) + 1
        category = cell(row, hm, "类别")
        description = cell(row, hm, "详细描述（STAR 法则")
        achievements = cell(row, hm, "量化成果")
        if not description:
            warnings.append(f"经历「{title}」缺少详细描述，已用角色/成果兜底（建议补齐）")
            description = f"{title}：{cell(row, hm, '角色 / 职位') or '参与'}，{achievements or '详见成果'}"
        skills = cell(row, hm, "关键技能")
        target = cell(row, hm, "适用岗位")
        materials.append({
            "id": f"exp_{seq}",
            "type": classify_experience(category),
            "title": title,
            "organization": cell(row, hm, "组织 / 机构"),
            "role": cell(row, hm, "角色 / 职位"),
            "start_date": norm_date(cell(row, hm, "开始时间")),
            "end_date": norm_date(cell(row, hm, "结束时间")),
            "description": description,
            "achievements": achievements,
            "skills": skills,
            "keywords": "、".join(x for x in (skills, target) if x),
            "target_directions": target,
            "source": cell(row, hm, "证明材料"),
            "resume_allowed": "是",
            "priority": stars_to_priority(cell(row, hm, "重要程度")),
            "notes": (f"原类别：{category}；{cell(row, hm, '备注')}")[:500],
        })

    # ---------- 🏆 奖项 ----------
    sheet = wb["🏆 奖项"]
    rows = list(sheet.iter_rows(values_only=True))
    hm = {h.strip(): i for i, h in enumerate(rows[0]) if h}
    for col in ("奖项全称", "获奖时间"):
        if col not in hm:
            raise SystemExit(f"失败：🏆 奖项表缺少列「{col}」")
    for row in rows[1:]:
        title = cell(row, hm, "奖项全称")
        if not title:
            continue
        seq = cell(row, hm, "序号") or len(materials) + 1
        level = cell(row, hm, "级别")
        rank = cell(row, hm, "排名/等级")
        ratio = cell(row, hm, "获奖比例")
        description = cell(row, hm, "描述 / 参赛作品")
        if not description:
            description = f"{title}（{level or '奖项'}）"
        achievements = "；".join(
            x for x in (level, f"排名/等级：{rank}" if rank else "", f"获奖比例：{ratio}" if ratio else "") if x
        )
        materials.append({
            "id": f"awd_{seq}",
            "type": "award",
            "title": title,
            "organization": cell(row, hm, "颁发机构"),
            "role": "",
            "start_date": norm_date(cell(row, hm, "获奖时间")),
            "end_date": "",
            "description": description,
            "achievements": achievements,
            "skills": "",
            "keywords": level,
            "target_directions": cell(row, hm, "适用岗位"),
            "source": cell(row, hm, "证明材料"),
            "resume_allowed": "是",
            "priority": stars_to_priority(cell(row, hm, "重要程度")),
            "notes": cell(row, hm, "备注")[:500],
        })

    # ---------- 📜 证明 ----------
    sheet = wb["📜 证明"]
    rows = list(sheet.iter_rows(values_only=True))
    hm = {h.strip(): i for i, h in enumerate(rows[0]) if h}
    if "证书全称" not in hm:
        raise SystemExit("失败：📜 证明表缺少列「证书全称」")
    for row in rows[1:]:
        title = cell(row, hm, "证书全称")
        if not title:
            continue
        seq = cell(row, hm, "序号") or len(materials) + 1
        category = cell(row, hm, "类别")
        score = cell(row, hm, "分数 / 等级")
        cert_no = cell(row, hm, "证书编号")
        description = f"证书类别：{category or '证书'}"
        if score:
            description += f"；分数/等级：{score}"
        if cert_no:
            description += f"；证书编号：{cert_no}"
        materials.append({
            "id": f"cert_{seq}",
            "type": "certification",
            "title": title,
            "organization": cell(row, hm, "颁发机构"),
            "role": "",
            "start_date": norm_date(cell(row, hm, "获取时间")),
            "end_date": norm_date(cell(row, hm, "有效期至")),
            "description": description,
            "achievements": score,
            "skills": "",
            "keywords": category,
            "target_directions": cell(row, hm, "适用岗位"),
            "source": cell(row, hm, "证明材料路径"),
            "resume_allowed": "是",
            "priority": stars_to_priority(cell(row, hm, "重要程度")),
            "notes": cell(row, hm, "备注")[:500],
        })

    wb.close()

    # ---------- 校验 ----------
    ids = [m["id"] for m in materials]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise SystemExit(f"失败：存在重复 id：{'、'.join(duplicates)}")
    missing_desc = [m["id"] for m in materials if not m["description"]]
    if missing_desc:
        raise SystemExit(
            f"失败：以下 {len(missing_desc)} 条素材缺少描述（事实必须完整，请补齐后重试）：{'、'.join(missing_desc)}"
        )
    return materials, warnings


def build_output(materials) -> bytes:
    out = Workbook()
    sheet = out.active
    sheet.title = "素材库"
    sheet.append(HEADERS)
    for m in materials:
        sheet.append([
            m["id"], m["type"], m["title"], m["organization"], m["role"],
            m["start_date"], m["end_date"], m["description"], m["achievements"],
            m["skills"], m["keywords"], m["target_directions"], m["source"],
            m["resume_allowed"], m["priority"], m["notes"],
        ])
    buf = io.BytesIO()
    out.save(buf)
    return buf.getvalue()


def main():
    parser = argparse.ArgumentParser(description="用户自维护素材表 → 标准素材库格式")
    parser.add_argument("--input", required=True, help="源 XLSX（📋 经历/🏆 奖项/📜 证明 三表）")
    parser.add_argument("--output", required=True, help="目标 XLSX（标准素材库）")
    parser.add_argument("--force", action="store_true", help="目标已存在时允许覆盖")
    parser.add_argument("--dry-run", action="store_true", help="只解析并输出统计，不写文件")
    args = parser.parse_args()

    src = Path(args.input)
    dst = Path(args.output)
    if dst.exists() and not args.force and not args.dry_run:
        raise SystemExit(f"失败：目标已存在：{dst}（使用 --force 覆盖）")

    materials, warnings = parse_source(src)

    for w in warnings:
        print(f"⚠ {w}")
    type_dist: dict[str, int] = {}
    for m in materials:
        type_dist[m["type"]] = type_dist.get(m["type"], 0) + 1
    print(f"解析成功：{len(materials)} 条素材")
    print(f"  类型分布: {type_dist}")

    if args.dry_run:
        print("dry-run：未写文件")
        return

    payload = build_output(materials)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(payload)
        handle.flush()
    tmp.replace(dst)
    print(f"✓ 转换完成：{len(materials)} 条素材 → {dst}")


if __name__ == "__main__":
    main()
