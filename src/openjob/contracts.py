"""产品契约单一事实来源（WP-S0）。

岗位状态与标签、状态迁移白名单、平台能力、事实来源类型、任务生命周期的
唯一权威定义。本模块必须是纯常量与纯函数：不导入 db / server / AI 模块，
避免循环依赖，任何一层都可以安全引用。

- 前端 `web/frontend/src/lib/status.ts` 不手抄维护第二份真值：
  由 tests/test_contracts.py 的一致性断言守护（漂移即测试红灯）。
- 完整契约说明见 docs/developer-contract.md。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 岗位状态（jobs.status）
# ---------------------------------------------------------------------------

JOB_STATUS_LABELS: dict[str, str] = {
    "pending": "待评分",
    "scored": "已评分",
    "filtered": "已过滤",
    "ready": "待确认",
    "approved": "已确认",
    "skipped": "已跳过",
    "sent": "已发送",
    "replied": "已回复",
    "resume_sent": "简历已发",
    "needs_resume": "待手动发简历",
    "follow_up_sent": "已跟进",
    "rejected": "已拒绝",
    "error": "发送失败",
    "stale": "超期退出",
    "interview": "面试中",
    "offer": "Offer",
    "hr_rejected": "HR拒绝",
    "closed": "已关闭",
}

# 终态之外保留的准入状态：manual_sent 是 history 动作（人工平台外发送台账），
# 不是岗位状态。stale（B9 审批超期退出，可重激活）与 interview/offer/closed/
# hr_rejected（A2 投后终态）自本版本起启用写入路径。
JOB_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"scored", "filtered", "ready", "error", "rejected"}),
    "scored": frozenset({"ready", "filtered", "error", "rejected"}),
    "filtered": frozenset({"ready", "filtered", "error", "rejected"}),
    "ready": frozenset({"approved", "sent", "rejected", "skipped", "error", "filtered"}),
    "approved": frozenset({"ready", "sent", "error", "rejected", "skipped", "stale"}),
    "sent": frozenset({"replied", "resume_sent", "needs_resume", "follow_up_sent", "rejected", "stale"}),
    "error": frozenset({"sent", "error"}),
    "needs_resume": frozenset({"resume_sent", "replied", "rejected", "follow_up_sent", "stale"}),
    "resume_sent": frozenset({"replied", "rejected", "needs_resume", "follow_up_sent", "stale"}),
    "follow_up_sent": frozenset({"replied", "resume_sent", "needs_resume", "rejected", "stale"}),
    "replied": frozenset({"interview", "hr_rejected", "closed"}),
    "interview": frozenset({"offer", "closed", "hr_rejected"}),
    "offer": frozenset({"closed"}),
    "hr_rejected": frozenset({"closed"}),
    "rejected": frozenset(),
    "skipped": frozenset(),
    "stale": frozenset({"ready", "approved"}),
    "closed": frozenset(),
}

# 智联/前程无忧的人工“标记已发送”是用户显式确认的平台外动作，
# 允许从任意未完成状态进入 sent（via="manual_external"）。
MANUAL_EXTERNAL_SEND_SOURCES: frozenset[str] = frozenset(
    {"pending", "scored", "filtered", "ready", "approved", "error"}
)

_VIA_MANUAL_EXTERNAL = "manual_external"


class IllegalJobStatusTransition(ValueError):
    """非法岗位状态迁移；调用方应保留原状态并向用户展示可读原因。"""

    def __init__(self, current: str, new: str, via: str = "standard"):
        self.current = current
        self.new = new
        self.via = via
        super().__init__(
            f"岗位状态不允许从「{JOB_STATUS_LABELS.get(current, current)}」"
            f"变更为「{JOB_STATUS_LABELS.get(new, new)}」（当前 via={via}）；原状态已保留"
        )


def validate_job_status_transition(current: str, new: str, *, via: str = "standard") -> None:
    """校验一次岗位状态迁移；非法迁移抛出 IllegalJobStatusTransition。

    同状态重写（幂等重放）始终允许。
    """
    current = str(current or "")
    new = str(new or "")
    if new not in JOB_STATUS_TRANSITIONS:
        raise IllegalJobStatusTransition(current, new, via)
    if current not in JOB_STATUS_TRANSITIONS:
        # 历史遗留的未知状态：允许写入一次以完成收敛，同时禁止迁移到终态以外的任意跳跃。
        raise IllegalJobStatusTransition(current, new, via)
    if current == new:
        return
    if via == _VIA_MANUAL_EXTERNAL and new == "sent" and current in MANUAL_EXTERNAL_SEND_SOURCES:
        return
    if new not in JOB_STATUS_TRANSITIONS[current]:
        raise IllegalJobStatusTransition(current, new, via)


# ---------------------------------------------------------------------------
# 任务生命周期（workbench / scheduled runs）
# ---------------------------------------------------------------------------

TASK_STATUS_LABELS: dict[str, str] = {
    "created": "已创建",
    "preflight": "启动前检查",
    "running": "运行中",
    "stopping": "停止中",
    "pausing": "暂停中",
    "paused": "已暂停",
    "completed": "已完成",
    "stopped": "已停止",
    "failed": "已失败",
    "interrupted": "已中断",
}

# 目标生命周期：created → preflight → running → (completed|stopped|failed)，支持
# pausing → paused（可恢复）与重启收敛 running → interrupted（不伪装成功）。
# 当前 workbench 任务已实现 running/stopping/completed/failed/stopped 与 finished
# 元数据；preflight/paused 状态位留给 WP-C7 任务生命周期落地时接入。
TASK_LIFECYCLE_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"preflight", "running", "stopped", "failed"}),
    "preflight": frozenset({"running", "failed", "stopped"}),
    "running": frozenset({"pausing", "completed", "stopped", "failed", "interrupted"}),
    "stopping": frozenset({"completed", "stopped", "failed", "interrupted"}),
    "pausing": frozenset({"paused", "stopped", "failed", "interrupted"}),
    "paused": frozenset({"running", "stopped", "interrupted"}),
    "completed": frozenset(),
    "stopped": frozenset(),
    "failed": frozenset(),
    "interrupted": frozenset(),
}

TASK_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "stopped", "failed", "interrupted"}
)

# 计划采集运行（scheduled_collection_runs.status）。工作台重启时无法确认的
# running 统一收敛为 interrupted，而不是伪装成 stopped/completed。
SCHEDULED_RUN_STATUSES: frozenset[str] = frozenset(
    {"claimed", "running", "skipped", "completed", "failed", "stopped", "interrupted"}
)

SCHEDULED_RUN_LABELS: dict[str, str] = {
    "claimed": "已认领",
    "running": "运行中",
    "skipped": "已跳过",
    "completed": "已完成",
    "failed": "已失败",
    "stopped": "已停止",
    "interrupted": "已中断",
}


# ---------------------------------------------------------------------------
# 平台能力（服务端单一来源仍是 collection/capabilities.py，此处转出口）
# ---------------------------------------------------------------------------

from openjob.collection.capabilities import (  # noqa: E402
    PLATFORM_CAPABILITIES,
    platform_supports,
)

PLATFORM_LABELS: dict[str, str] = {
    "boss": "BOSS直聘",
    "zhilian": "智联招聘",
    "51job": "前程无忧",
}


# ---------------------------------------------------------------------------
# 事实来源类型（WP-S1 事实安全；执行细节见 ai/fact_policy.py）
# ---------------------------------------------------------------------------

FACT_SOURCE_TYPES_ALLOWED: frozenset[str] = frozenset(
    {
        "verified_base_resume",      # 已验证简历底稿（base_resumes 或用户上传文件）
        "verified_resume_material",  # 已确认素材条目（resume_allowed=是）
        "user_confirmed_fact",       # 用户界面明确确认的编辑（记录确认时间）
    }
)

FACT_SOURCE_TYPES_FORBIDDEN: frozenset[str] = frozenset(
    {
        "example_resume",             # resume.md / resume.example.md / 示例数据
        "job_description",            # 岗位 JD：只提供匹配目标，不是用户事实
        "ai_score_reason",            # AI 评分理由
        "generated_resume",           # AI 生成简历（岗位版本产物，不可反哺）
        "platform_default_greeting",  # 平台默认招呼语
        "unconfirmed_import",         # 未确认的导入行
        "user_preference",            # 用户偏好中的身份性描述
    }
)

# 招呼语事实状态（jobs.greeting_fact_status）
GREETING_FACT_STATUSES: frozenset[str] = frozenset(
    {"verified", "unverified", "failed", "stale"}
)
