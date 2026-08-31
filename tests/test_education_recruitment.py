import tempfile
from pathlib import Path

from openjob.ai.scorer import _build_scoring_prompt
from openjob.collection.models import JobCandidate, classify_recruitment_type
from openjob.db import get_db, insert_job_if_new


def test_recruitment_type_classification_is_conservative():
    assert classify_recruitment_type("应届产品经理", "经验不限", "") == "campus"
    assert classify_recruitment_type("产品经理", "3-5年", "") == "experienced"
    assert classify_recruitment_type("产品经理", "", "公司成立于2024年") == "unknown"


def test_recruitment_type_uses_salary_over_campus_keywords():
    # 面向应届生但月薪 K 格式 → 正式岗（用户报告的误分类场景）
    assert classify_recruitment_type(
        "【接受无经验，应届优先】小红书运营岗", "经验不限", "负责内容运营", "7-11K"
    ) == "experienced"
    assert classify_recruitment_type("运营助理/可应届（包吃）", "经验不限", "", "4-8K") == "experienced"
    # 标题含实习 → 实习（即使 JD 提到转正后薪资）
    assert classify_recruitment_type(
        "数据分析实习生", "经验不限", "转正后 8-12K", "90-100元/天"
    ) == "campus"
    # 日薪格式 → 实习
    assert classify_recruitment_type("商业运营", "经验不限", "", "120-150元/天") == "campus"
    # 无薪资的校招/应届 → 保守归实习侧
    assert classify_recruitment_type("校园招聘产品经理", "应届毕业生", "面向应届毕业生") == "campus"
    # 管培生是正式岗
    assert classify_recruitment_type("管理培训生", "应届", "", "6-8K") == "experienced"


def test_platform_job_record_keeps_education_and_recruitment_type():
    record = JobCandidate(
        platform="boss",
        source_job_id="education-1",
        title="校园招聘产品经理",
        company="示例公司",
        education="本科",
        jd="面向应届毕业生",
    ).as_job_record()

    assert record["education"] == "本科"
    assert record["recruitment_type"] == "campus"


def test_database_stores_structured_education_fields():
    with tempfile.TemporaryDirectory() as temporary:
        db = get_db(Path(temporary) / "jobs.db")
        try:
            inserted = insert_job_if_new(db, {
                "id": "education-db-1",
                "title": "数据分析师",
                "company": "示例公司",
                "education": "硕士",
                "salary": "10-15K",
                "recruitment_type": "experienced",
                "source_platform": "boss",
                "source_job_id": "education-db-1",
            })
            row = db.execute(
                "SELECT education, recruitment_type FROM jobs WHERE id = ?",
                ("education-db-1",),
            ).fetchone()
        finally:
            db.close()

    assert inserted is True
    # recruitment_type 由入库时的统一分类器重算（月薪 K → 正式岗）
    assert dict(row) == {"education": "硕士", "recruitment_type": "experienced"}


def test_scoring_prompt_includes_candidate_and_job_recruitment_context():
    prompt = _build_scoring_prompt(
        {
            "title": "产品经理",
            "company": "示例公司",
            "salary": "20-30K",
            "experience": "3-5年",
            "education": "本科",
            "recruitment_type": "experienced",
            "jd": "负责产品规划。",
        },
        "候选人简历",
        {"profile": {"education": "硕士", "recruitment_type": "both"}},
    )

    assert "最高学历：硕士" in prompt
    assert "求职招聘类型：校招/社招均可" in prompt
    assert "学历要求：本科" in prompt
    assert "招聘类型：社招" in prompt
