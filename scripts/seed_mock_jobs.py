"""联调载体：向岗位池灌入确定性生成的演示岗位（无需真实平台/网络）。

用途：开发/演示 Web UI 与流水线时，不用真实采集即可获得一批稳定数据。
用法：
    python scripts/seed_mock_jobs.py [数量]
数据由种子随机生成，每次运行结果稳定；重复运行会按 URL 去重，不会产生重复岗位。
生成岗位带 `mock` 标记（公司名后缀），与真实数据可区分。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openjob.db import get_db, insert_job  # noqa: E402

_COMPANIES = ["云启科技", "星澜网络", "矩阵互娱", "青禾数据", "未来工厂"]
_TITLES = ["数据分析实习生", "运营实习生", "数据运营实习生", "商业分析实习生"]
_CITIES = ["广州", "佛山"]
_SKILLS = ["Python", "SQL", "Excel", "Tableau", "数据看板", "用户增长", "小红书"]
_WELFARE = ["五险一金", "双休", "转正机会", "包吃", "弹性工作"]


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    import random

    rng = random.Random(42)  # 固定种子：结果确定性
    db = get_db()
    added = 0
    for i in range(count):
        company = _COMPANIES[i % len(_COMPANIES)]
        title = _TITLES[i % len(_TITLES)]
        city = _CITIES[i % len(_CITIES)]
        skills = rng.sample(_SKILLS, k=3)
        welfare = rng.sample(_WELFARE, k=2)
        job_id = f"mock-{city}-{i:03d}"
        jd = (
            f"岗位职责：负责{title.replace('实习生', '')}相关工作，使用 {'、'.join(skills[:2])} 完成"
            f"日常数据整理与分析，输出业务报告。任职要求：熟悉 {'、'.join(skills)}，"
            f"细心负责，{'、'.join(welfare)}。"
        )
        inserted = insert_job(db, {
            "id": job_id,
            "title": title,
            "company": f"{company}（mock）",
            "salary": f"{80 + i * 10}-{120 + i * 10}元/天",
            "city": city,
            "experience": "经验不限",
            "education": "本科",
            "jd": jd,
            "url": f"https://example.com/mock/{job_id}",
            "source_platform": "boss",
            "source_job_id": job_id,
        })
        added += int(inserted)
    db.close()
    print(f"演示岗位：新增 {added} 个（请求 {count} 个，其余已存在被去重）")


if __name__ == "__main__":
    main()
