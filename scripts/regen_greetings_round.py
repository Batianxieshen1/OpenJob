"""分轮续跑批量重生成：每轮最多 N 个，避免超过后台任务 10 分钟窗口。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openjob.config import load_config
from openjob.db import get_db
from openjob.ai.greeter import generate_greetings

BATCH_SIZE = 12


def pending_ids() -> list[str]:
    db = get_db(Path(__file__).resolve().parents[1] / "data" / "openjob.db")
    rows = db.execute(
        "SELECT id FROM jobs WHERE status='approved' AND deleted_at IS NULL "
        "AND (greeting IS NULL OR TRIM(greeting) = '')"
    ).fetchall()
    db.close()
    return [r["id"] for r in rows]


def main() -> None:
    ids = pending_ids()[:BATCH_SIZE]
    if not ids:
        print("ROUND_DONE: 没有待生成岗位", flush=True)
        return
    config = load_config()
    config["_workbench_job_ids"] = ids
    print(f"本轮生成 {len(ids)} 个", flush=True)
    count = generate_greetings(config)
    remaining = len(pending_ids())
    print(f"本轮完成 {count}，剩余 {remaining}", flush=True)


if __name__ == "__main__":
    main()
