"""素材库 XLSX 解析、schema 校验、哈希与索引测试。"""

import json
from io import BytesIO

import pytest

from openjob.resume_materials import (
    DEFAULT_HEADERS,
    MaterialLibraryError,
    build_template_workbook,
    load_or_refresh_library,
    parse_workbook,
    save_library_atomically,
)


def build_workbook(rows, headers=None):
    from openpyxl import Workbook
    headers = headers or DEFAULT_HEADERS
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "素材库"
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(h, "") for h in headers])
    buf = BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def test_parse_valid_workbook_normalizes_rows_and_dates():
    content = build_workbook([{
        "id": "exp_001", "type": "experience", "title": "内容运营",
        "organization": "某公司", "role": "实习生", "start_date": "2024-03",
        "end_date": "2024-08", "description": "策划公众号专题并复盘数据",
        "achievements": "阅读量提升 30%", "skills": "内容策划, 数据分析",
        "keywords": "公众号，增长", "target_directions": "运营",
        "resume_allowed": "是", "priority": 5,
    }])
    library = parse_workbook(content, filename="素材.xlsx")
    assert library.count == 1
    assert library.items[0]["id"] == "exp_001"
    assert library.items[0]["resume_allowed"] is True
    assert library.items[0]["start_date"] == "2024-03"
    assert library.items[0]["skills"] == ["内容策划", "数据分析"]
    assert library.items[0]["keywords"] == ["公众号", "增长"]
    assert library.sha256
    assert library.updated_at


def test_parse_rejects_missing_columns_duplicate_ids_and_invalid_values():
    with pytest.raises(MaterialLibraryError, match="缺少必需列"):
        parse_workbook(build_workbook([], headers=["id", "title"]), filename="素材.xlsx")
    rows = [{"id": "same", "type": "award", "title": "奖项", "description": "事实", "resume_allowed": "是"}]
    with pytest.raises(MaterialLibraryError, match="重复 id"):
        parse_workbook(build_workbook(rows + rows), filename="素材.xlsx")
    bad = [{"id": "x", "type": "unknown", "title": "x", "description": "事实", "resume_allowed": "是"}]
    with pytest.raises(MaterialLibraryError, match="type"):
        parse_workbook(build_workbook(bad), filename="素材.xlsx")


def test_parse_rejects_empty_workbook_and_invalid_extension():
    with pytest.raises(MaterialLibraryError, match="至少需要一条"):
        parse_workbook(build_workbook([]), filename="素材.xlsx")
    with pytest.raises(MaterialLibraryError, match="xlsx"):
        parse_workbook(b"not-xlsx", filename="素材.xls")


def test_parse_rejects_bad_dates_bool_priority_and_end_before_start():
    with pytest.raises(MaterialLibraryError, match="start_date"):
        parse_workbook(build_workbook([{
            "id": "a", "type": "project", "title": "t", "description": "d",
            "start_date": "2024/03", "resume_allowed": "是",
        }]), filename="素材.xlsx")
    with pytest.raises(MaterialLibraryError, match="resume_allowed"):
        parse_workbook(build_workbook([{
            "id": "a", "type": "project", "title": "t", "description": "d",
            "resume_allowed": "maybe",
        }]), filename="素材.xlsx")
    with pytest.raises(MaterialLibraryError, match="priority"):
        parse_workbook(build_workbook([{
            "id": "a", "type": "project", "title": "t", "description": "d",
            "resume_allowed": "是", "priority": 9,
        }]), filename="素材.xlsx")
    with pytest.raises(MaterialLibraryError, match="end_date"):
        parse_workbook(build_workbook([{
            "id": "a", "type": "project", "title": "t", "description": "d",
            "start_date": "2024-08", "end_date": "2024-01", "resume_allowed": "是",
        }]), filename="素材.xlsx")


def test_parse_rejects_invalid_id_and_oversize_text():
    with pytest.raises(MaterialLibraryError, match="id"):
        parse_workbook(build_workbook([{
            "id": "bad id!", "type": "project", "title": "t", "description": "d",
            "resume_allowed": "是",
        }]), filename="素材.xlsx")
    with pytest.raises(MaterialLibraryError, match="description"):
        parse_workbook(build_workbook([{
            "id": "a", "type": "project", "title": "t", "description": "x" * 2001,
            "resume_allowed": "是",
        }]), filename="素材.xlsx")


def test_template_workbook_parses_as_example_library():
    template = build_template_workbook()
    library = parse_workbook(template, filename="template.xlsx")
    assert library.count == 1  # 示例行可解析，用户替换后即为正式素材


def test_atomic_save_writes_xlsx_and_index(tmp_path):
    content = build_workbook([{
        "id": "m1", "type": "award", "title": "市级奖项", "description": "调研作品获奖",
        "resume_allowed": "是",
    }])
    library = parse_workbook(content, filename="素材.xlsx")
    xlsx_path = tmp_path / "resume_materials.xlsx"
    index_path = tmp_path / "resume_materials.index.json"
    save_library_atomically(library, content, xlsx_path=xlsx_path, index_path=index_path)

    assert xlsx_path.read_bytes() == content
    data = json.loads(index_path.read_text(encoding="utf-8"))
    assert data["sha256"] == library.sha256
    assert data["items"][0]["id"] == "m1"
    assert not list(tmp_path.glob("*.tmp"))


def test_load_or_refresh_reuses_index_and_reparses_on_hash_change(tmp_path):
    content = build_workbook([{
        "id": "m1", "type": "award", "title": "奖项A", "description": "d",
        "resume_allowed": "是",
    }])
    xlsx_path = tmp_path / "resume_materials.xlsx"
    index_path = tmp_path / "resume_materials.index.json"
    library = parse_workbook(content, filename="素材.xlsx")
    save_library_atomically(library, content, xlsx_path=xlsx_path, index_path=index_path)

    cached = load_or_refresh_library(xlsx_path, index_path)
    assert cached.count == 1

    # 外部修改 XLSX（换内容）→ 哈希变化 → 自动重解析
    new_content = build_workbook([{
        "id": "m1", "type": "award", "title": "奖项B", "description": "d2",
        "resume_allowed": "是",
    }])
    xlsx_path.write_bytes(new_content)
    refreshed = load_or_refresh_library(xlsx_path, index_path)
    assert refreshed.items[0]["title"] == "奖项B"
    assert refreshed.sha256 != library.sha256

    # 索引损坏 → 重新解析恢复
    index_path.write_text("{broken", encoding="utf-8")
    recovered = load_or_refresh_library(xlsx_path, index_path)
    assert recovered.items[0]["title"] == "奖项B"

    # 文件缺失 → 明确报错
    missing = tmp_path / "nope.xlsx"
    with pytest.raises(MaterialLibraryError, match="未找到简历素材库"):
        load_or_refresh_library(missing, index_path)


def test_resolve_library_paths_default_custom_and_out_of_bounds(tmp_path):
    from openjob.resume_materials import resolve_library_paths

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # 默认：./data/resume_materials.xlsx 相对项目根解析，落在 data_dir 内
    x, i = resolve_library_paths({"profile": {}}, data_dir)
    assert x == data_dir / "resume_materials.xlsx"
    assert i == data_dir / "resume_materials.index.json"

    # 自定义文件名：data/ 内
    x, i = resolve_library_paths(
        {"profile": {"resume_materials_path": "./data/custom-materials.xlsx"}}, data_dir
    )
    assert x == data_dir / "custom-materials.xlsx"
    assert i == data_dir / "custom-materials.index.json"

    # 相对越界：../outside.xlsx 拒绝
    with pytest.raises(MaterialLibraryError, match="data 目录"):
        resolve_library_paths(
            {"profile": {"resume_materials_path": "../outside.xlsx"}}, data_dir
        )

    # 绝对路径越界：拒绝
    outside = tmp_path / "outside" / "m.xlsx"
    outside.parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(MaterialLibraryError, match="data 目录"):
        resolve_library_paths(
            {"profile": {"resume_materials_path": str(outside)}}, data_dir
        )

    # data 内子目录：允许
    x, i = resolve_library_paths(
        {"profile": {"resume_materials_path": "./data/sub/dir/m.xlsx"}}, data_dir
    )
    assert x == data_dir / "sub" / "dir" / "m.xlsx"
