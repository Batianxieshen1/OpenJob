"""市场分析统计：从岗位池聚合九个维度的分布。

全部纯 Python 内存计算（岗位池几百条，毫秒级），不引入额外依赖。
"""

import re
from collections import Counter

from openjob.job_filters import parse_monthly_salary_k

# 技能关键词：出现即计数（大小写不敏感）
SKILL_KEYWORDS = [
    "Python", "SQL", "Excel", "Tableau", "Power BI", "SPSS", "Hive", "Hadoop",
    "数据分析", "数据清洗", "可视化", "爬虫", "机器学习", "深度学习", "用户增长",
    "用户运营", "内容运营", "社群运营", "私域", "小红书", "抖音", "直播", "电商",
    "A/B", "埋点", "RFM", "用户画像", "报表", "看板", "市场调研", "竞品分析",
]

# 福利关键词：JD 中出现即计数
WELFARE_KEYWORDS = [
    "五险一金", "六险一金", "双休", "单休", "大小周", "包吃", "包住", "餐补",
    "房补", "交通补助", "弹性工作", "弹性办公", "年终奖", "带薪年假", "节日福利",
    "转正", "加班", "免费班車", "下午茶", "期权", "股票",
]

_SALARY_BUCKETS = [
    ("3K以下", lambda k: k < 3),
    ("3-5K", lambda k: 3 <= k < 5),
    ("5-8K", lambda k: 5 <= k < 8),
    ("8-12K", lambda k: 8 <= k < 12),
    ("12-20K", lambda k: 12 <= k < 20),
    ("20K以上", lambda k: k >= 20),
    ("日薪实习", None),  # 单独归类
]


def _bucket_salary(salary: str) -> str:
    if not salary:
        return "未标注"
    if re.search(r"元\s*/\s*天", salary) or "元/时" in salary or "元/小时" in salary:
        return "日薪实习"
    parsed = parse_monthly_salary_k(salary)
    if parsed:
        low, _high = parsed
        for label, check in _SALARY_BUCKETS[:-1]:
            if check(low):
                return label
        return "20K以上"
    return "未标注"


def _experience_group(experience: str) -> str:
    text = str(experience or "")
    if not text.strip():
        return "未标注"
    if any(k in text for k in ("不限", "应届", "在校", "无经验")):
        return "经验不限/应届"
    if re.search(r"[1１]\s*[-–~至]?\s*[3３]?年?", text) and "3" not in text:
        return "1-3年"
    if "3" in text and "5" in text:
        return "3-5年"
    if "5" in text or "10" in text:
        return "5年以上"
    if "年" in text:
        return "1-3年"
    return "未标注"


def compute_market_stats(rows: list[dict]) -> dict:
    """rows: jobs 表相关字段的 dict 列表（未过滤的岗位）。"""
    total = len(rows)
    if total == 0:
        return {"total": 0}

    platform = Counter(str(r.get("source_platform") or "boss") for r in rows)
    city = Counter(str(r.get("city") or "未标注") for r in rows)
    salary = Counter(_bucket_salary(str(r.get("salary") or "")) for r in rows)
    education = Counter(str(r.get("education") or "未标注") for r in rows)
    experience = Counter(_experience_group(str(r.get("experience") or "")) for r in rows)
    recruitment = Counter(str(r.get("recruitment_type") or "unknown") for r in rows)
    company = Counter(str(r.get("company") or "") for r in rows)

    jd_texts = [str(r.get("jd") or "") for r in rows]
    skill_freq = Counter()
    welfare_freq = Counter()
    for text in jd_texts:
        lowered = text.lower()
        for kw in SKILL_KEYWORDS:
            n = lowered.count(kw.lower())
            if n:
                skill_freq[kw] += 1
        for kw in WELFARE_KEYWORDS:
            if kw in text:
                welfare_freq[kw] += 1

    def top(counter: Counter, n: int) -> list[dict]:
        return [{"name": k, "count": v} for k, v in counter.most_common(n)]

    salary_order = [label for label, _ in _SALARY_BUCKETS] + ["未标注"]
    salary_list = [{"name": k, "count": salary[k]} for k in salary_order if salary[k] > 0]

    return {
        "total": total,
        "platform": top(platform, 5),
        "city": top(city, 10),
        "salary": salary_list,
        "education": top(education, 8),
        "experience": top(experience, 6),
        "recruitment": top(recruitment, 3),
        "top_companies": top(company, 12),
        "skill_freq": top(skill_freq, 15),
        "welfare_freq": top(welfare_freq, 12),
    }
