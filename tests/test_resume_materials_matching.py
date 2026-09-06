"""素材候选排序、方向/关键词命中与 AI 选择合同测试。"""

import pytest

from openjob.ai.resume_engine.materials import (
    Candidate,
    material_prompt,
    rank_materials,
    validate_material_ids,
)
from openjob.ai.resume_engine.models import JdProfile


def _jd(title: str = "数据分析实习生") -> JdProfile:
    return JdProfile.from_payload({
        "title": title,
        "summary": "负责业务数据分析与看板搭建",
        "keywords": ["SQL", "Python", "看板"],
        "hard_requirements": [
            {"skill": "SQL", "weight": 5, "required": True, "source_evidence": "要求 SQL"},
        ],
        "preferred_skills": [
            {"skill": "小红书运营", "weight": 3, "required": False, "source_evidence": "熟悉小红书"},
        ],
    })


def test_rank_materials_prefers_direction_and_hard_skill_hits():
    jd = _jd()
    items = [
        {
            "id": "weak", "type": "experience", "title": "社团宣传",
            "description": "撰写文案", "keywords": ["运营"],
            "target_directions": ["运营"], "resume_allowed": True, "priority": 3,
        },
        {
            "id": "strong", "type": "project", "title": "业务数据看板",
            "description": "使用 SQL 和 Python 完成数据清洗与看板",
            "keywords": ["SQL", "Python", "看板"],
            "target_directions": ["数据分析"], "resume_allowed": True, "priority": 3,
        },
    ]
    result = rank_materials(items, jd, limit=8)
    assert result[0].material["id"] == "strong"
    assert any("硬性技能" in r or "关键词" in r for r in result[0].reasons)
    assert result[0].reasons


def test_rank_materials_excludes_forbidden_items_and_limits_candidates():
    items = [
        {
            "id": str(i), "type": "project", "title": f"项目{i}", "description": "SQL 数据处理",
            "resume_allowed": i != 2, "priority": 3,
        }
        for i in range(12)
    ]
    result = rank_materials(items, _jd(), limit=8)
    assert len(result) == 8
    assert all(item.material["id"] != "2" for item in result)


def test_rank_materials_prioritizes_on_priority_field():
    jd = _jd()
    low = {"id": "low", "type": "project", "title": "业务看板", "description": "SQL 看板",
           "resume_allowed": True, "priority": 1}
    high = {"id": "high", "type": "project", "title": "业务看板二", "description": "SQL 看板",
            "resume_allowed": True, "priority": 5}
    result = rank_materials([low, high], jd, limit=8)
    assert result[0].material["id"] == "high"


def test_validate_material_ids_rejects_unknown_or_non_candidate_ids():
    candidates = [Candidate(material={"id": "m1"}, score=10, reasons=["关键词命中"])]
    assert validate_material_ids(["m1"], candidates) == ["m1"]
    assert validate_material_ids(["m1", "m1", ""], candidates) == ["m1"]  # 去重去空
    with pytest.raises(ValueError, match="候选素材"):
        validate_material_ids(["m2"], candidates)


def test_material_prompt_hides_source_and_notes():
    candidates = [Candidate(
        material={
            "id": "m1", "type": "award", "title": "市级奖项",
            "description": "调研作品获奖", "source": "学校文件编号123",
            "notes": "私密备注", "achievements": "市级", "skills": ["问卷"],
        },
        score=30,
        reasons=["关键词命中"],
    )]
    prompt = material_prompt(candidates)
    assert "m1" in prompt
    assert "市级奖项" in prompt
    assert "学校文件编号123" not in prompt
    assert "私密备注" not in prompt


def test_material_prompt_empty_candidates_returns_empty_string():
    assert material_prompt([]) == ""


def test_validate_material_ids_enforces_max_four():
    from openjob.ai.resume_engine.materials import MAX_SELECTED_MATERIALS

    candidates = [
        Candidate(material={"id": f"m{i}"}, score=10, reasons=[]) for i in range(6)
    ]
    four = validate_material_ids(["m0", "m1", "m2", "m3"], candidates)
    assert len(four) == 4

    # 重复去重后 5 条仍拒绝
    with pytest.raises(ValueError, match="最多只能引用"):
        validate_material_ids(["m0", "m0", "m1", "m2", "m3", "m4"], candidates)

    with pytest.raises(ValueError, match="候选素材"):
        validate_material_ids(["nope"], candidates)
