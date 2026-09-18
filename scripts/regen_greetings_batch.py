"""批量重生成招呼语后台任务（无发送路径）：读取 data/regen_batch.json 的岗位清单。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openjob.config import load_config
from openjob.db import get_db
from openjob.ai.greeter import generate_greetings


def main() -> None:
    base = Path(__file__).resolve().parents[1]
    job_ids = json.loads((base / "data" / "regen_batch.json").read_text(encoding="utf-8"))
    config = load_config()
    config["_workbench_job_ids"] = job_ids
    print(f"批量重生成启动：{len(job_ids)} 个岗位", flush=True)
    count = generate_greetings(config)
    print(f"批量重生成完成：成功 {count}/{len(job_ids)}", flush=True)

    # 落一份结果摘要供汇报
    db = get_db(base / "data" / "openjob.db")
    rows = db.execute(
        f"SELECT company, title, score, greeting_fact_status, length(greeting) AS glen "
        f"FROM jobs WHERE id IN ({','.join('?' * len(job_ids))})",
        job_ids,
    ).fetchall()
    summary = {
        "total": len(job_ids),
        "generated": count,
        "verified": sum(1 for r in rows if r["greeting_fact_status"] == "verified"),
        "rows": [dict(r) for r in rows],
    }
    (base / "data" / "regen_result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    db.close()


if __name__ == "__main__":
    main()
