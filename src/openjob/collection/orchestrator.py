"""Shared filtering, atomic persistence and strictly serial collection queue."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import Event
from typing import Any, Callable
from uuid import uuid4

from openjob.collection.base import CollectionError, CollectorHooks
from openjob.collection.models import (
    COLLECTION_CHANNEL_LABELS,
    SUPPORTED_BOSS_CHANNELS,
    CollectionProgress,
    JobCandidate,
    PlatformCollectionRequest,
    PlatformCollectionResult,
)
from openjob.collection.platforms.boss import BossCollector
from openjob.collection.platforms.job51 import Job51Collector, get_51job_city_code
from openjob.collection.platforms.zhilian import ZhilianCollector, get_zhilian_city_code
from openjob.collection.registry import CollectorRegistry
from openjob.collection_run_store import create_collection_run, update_collection_run
from openjob.db import (
    get_db,
    insert_job_if_new,
    job_identity_exists,
    record_job_source_observation,
)
from openjob.job_filters import matching_blocked_company, matching_deal_breaker


SUPPORTED_PLATFORMS = {"boss", "zhilian", "51job"}
SORT_OPTIONS = {
    "boss": {"default", "newest"},
    "zhilian": {"default", "newest"},
    "51job": {"default"},
}


def _clean_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


# 回写键位约定（与 normalize_collection_options 的读取权威位一一对应）：
# - keywords/cities/city_codes/max_pages/sort/recruitment_filter → platforms.X.search（base 合并源）
# - boss 的 source_channels/recommendation_* → platforms.boss 顶层（channels holder 第三位，先于 search 合并对象）
# - 全部再镜像一份到顶层 search：CLI run/scrape 只读顶层，防两源漂移
PERSIST_SEARCH_KEYS = ("keywords", "cities", "city_codes", "max_pages", "sort", "recruitment_filter")
PERSIST_BOSS_CHANNEL_KEYS = (
    "source_channels",
    "recommendation_max_scrolls",
    "recommendation_max_cards",
    "recommendation_same_result_limit",
)


def persist_collection_preferences(config: dict[str, Any], collection_options: dict[str, Any]) -> None:
    """把归一化采集参数写回 config dict（原地修改），保证读写键位一致。"""
    collection = config.get("collection") if isinstance(config.get("collection"), dict) else {}
    collection["default_order"] = collection_options["platform_order"]
    collection["auto_score_default"] = collection_options["auto_score"]
    config["collection"] = collection

    platform_configs = deepcopy(config.get("platforms")) if isinstance(config.get("platforms"), dict) else {}
    selected = set(collection_options["platform_order"])
    mirror: dict[str, Any] = {}
    for platform, value in collection_options["platforms"].items():
        existing = platform_configs.get(platform) if isinstance(platform_configs.get(platform), dict) else {}
        entry = dict(existing)
        entry["enabled"] = platform in selected
        search_cfg = dict(entry.get("search")) if isinstance(entry.get("search"), dict) else {}
        for key in PERSIST_SEARCH_KEYS:
            if key in value:
                search_cfg[key] = value[key]
        for key in PERSIST_BOSS_CHANNEL_KEYS:
            if key in value:
                entry[key] = value[key]
                # 迁移清理：这些键的读取权威位在平台顶层，search 内嵌值是
                # 被遮蔽的死配置（2026-09-26 审计），回写时顺手清走。
                search_cfg.pop(key, None)
        entry["search"] = search_cfg
        platform_configs[platform] = entry
        if platform == "boss":
            for key in PERSIST_SEARCH_KEYS:
                if key in search_cfg:
                    mirror[key] = search_cfg[key]
    for platform in ("boss", "zhilian", "51job"):
        if platform not in selected and isinstance(platform_configs.get(platform), dict):
            platform_configs[platform]["enabled"] = False
    config["platforms"] = platform_configs
    if mirror:
        legacy = config.get("search") if isinstance(config.get("search"), dict) else {}
        config["search"] = {**legacy, **mirror}


def normalize_collection_options(config: dict[str, Any], raw_options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a validated collection request while keeping legacy BOSS config compatible."""
    supplied = deepcopy(raw_options) if isinstance(raw_options, dict) else {}
    raw_platforms = supplied.get("platforms") if isinstance(supplied.get("platforms"), dict) else {}
    configured_platforms = config.get("platforms") if isinstance(config.get("platforms"), dict) else {}
    legacy_search = config.get("search") if isinstance(config.get("search"), dict) else {}
    boss_search = configured_platforms.get("boss", {}).get("search", {}) if isinstance(configured_platforms.get("boss"), dict) else {}
    if not boss_search:
        boss_search = legacy_search
    elif isinstance(legacy_search, dict):
        # ``load_config`` supplies platform defaults even for old config.yaml
        # files. Non-empty legacy search values must still win over those empty
        # defaults, while explicit platform values remain authoritative.
        boss_search = dict(boss_search)
        for key, value in legacy_search.items():
            if value not in (None, "", [], {}):
                if boss_search.get(key) in (None, "", [], {}):
                    boss_search[key] = value
    if (
        isinstance(legacy_search, dict)
        and not raw_platforms.get("boss")
        and not (boss_search.get("keywords") or boss_search.get("cities"))
        and (legacy_search.get("keywords") or legacy_search.get("cities"))
    ):
        boss_search = dict(legacy_search)
    zhilian_search = configured_platforms.get("zhilian", {}).get("search", {}) if isinstance(configured_platforms.get("zhilian"), dict) else {}
    job51_search = configured_platforms.get("51job", {}).get("search", {}) if isinstance(configured_platforms.get("51job"), dict) else {}

    platforms: dict[str, Any] = {}
    for platform, fallback in (("boss", boss_search), ("zhilian", zhilian_search), ("51job", job51_search)):
        value = raw_platforms.get(platform) if isinstance(raw_platforms.get(platform), dict) else {}
        search = value.get("search") if isinstance(value.get("search"), dict) else value
        if not isinstance(search, dict):
            search = {}
        base = dict(fallback) if isinstance(fallback, dict) else {}
        base.update(search)
        if platform == "boss" and not base.get("cities"):
            base["cities"] = config.get("profile", {}).get("target_cities", ["北京"])
        platforms[platform] = {
            "keywords": _clean_strings(base.get("keywords")),
            "cities": _clean_strings(base.get("cities")),
            "city_codes": {
                str(key).strip(): str(value).strip()
                for key, value in (base.get("city_codes") or {}).items()
                if str(key).strip() and str(value).strip()
            } if isinstance(base.get("city_codes"), dict) else {},
            "max_pages": base.get("max_pages", 3 if platform == "boss" else 1),
            "sort": str(base.get("sort") or ("newest" if platform == "boss" else "default")),
            "recruitment_filter": str(base.get("recruitment_filter") or "").strip(),
        }
        if platform == "boss":
            # 来源通道契约（推荐页计划 Task 1）：默认 ["search"]；去重保序。
            # 取值优先级：raw.boss 顶层 → raw.boss.search → config.platforms.boss 顶层 → config.boss.search。
            # 通道合法性在 validate_collection_options 统一校验。
            channel_holders = [value, search, configured_platforms.get("boss") or {}, base]
            channels: list[str] | None = None
            params: dict[str, Any] = {}
            for holder in channel_holders:
                if not isinstance(holder, dict):
                    continue
                if channels is None and holder.get("source_channels") not in (None, "", [], {}):
                    channels = _clean_strings(holder.get("source_channels"))
                for key in ("recommendation_max_scrolls", "recommendation_max_cards", "recommendation_same_result_limit"):
                    if key not in params and holder.get(key) is not None:
                        params[key] = holder.get(key)
            if channels is None or not channels:
                channels = ["search"]
            platforms[platform]["source_channels"] = list(dict.fromkeys(channels))
            platforms[platform]["recommendation_max_scrolls"] = params.get("recommendation_max_scrolls", 4)
            platforms[platform]["recommendation_max_cards"] = params.get("recommendation_max_cards", 50)
            platforms[platform]["recommendation_same_result_limit"] = params.get("recommendation_same_result_limit", 2)

    order = supplied.get("platform_order")
    if order is None:
        configured_order = config.get("collection", {}).get("default_order") if isinstance(config.get("collection"), dict) else None
        order = configured_order if isinstance(configured_order, list) else ["boss"]
        if not supplied:
            enabled_platforms = {"boss"}
            for platform, value in configured_platforms.items():
                if isinstance(value, dict) and value.get("enabled"):
                    enabled_platforms.add(str(platform))
            order = [platform for platform in order if platform in enabled_platforms]
            if not order:
                order = ["boss"]
    order = [str(value).strip() for value in order if str(value).strip()] if isinstance(order, list) else []
    selected_platforms = supplied.get("platforms") if isinstance(supplied.get("platforms"), dict) else None
    if supplied and selected_platforms is not None:
        selected = {str(key).strip() for key in selected_platforms if str(key).strip()}
        order = [platform for platform in order if platform in selected]
    options = {
        "platform_order": order,
        "auto_score": supplied.get("auto_score", False) is True,
        "platforms": {platform: platforms[platform] for platform in order if platform in platforms},
    }
    return validate_collection_options(options)


def validate_collection_options(options: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(options, dict):
        raise ValueError("采集参数必须是对象")
    for platform_cfg in (options.get("platforms") or {}).values():
        if isinstance(platform_cfg, dict):
            value = str(platform_cfg.get("recruitment_filter") or "").strip()
            if value not in {"", "campus", "experienced"}:
                raise ValueError("招聘类型过滤只支持：空（全部）、campus（只有实习）、experienced（只有正式岗）")
    order = options.get("platform_order")
    platforms = options.get("platforms")
    if not isinstance(order, list) or not order:
        raise ValueError("至少选择一个采集平台")
    order = [str(value).strip() for value in order]
    if len(order) != len(set(order)):
        raise ValueError("采集平台顺序不能重复")
    if any(platform not in SUPPORTED_PLATFORMS for platform in order):
        raise ValueError("采集平台只支持 boss、zhilian 或 51job")
    if not isinstance(platforms, dict) or set(platforms) != set(order):
        raise ValueError("平台顺序与平台配置不一致")
    if not isinstance(options.get("auto_score", False), bool):
        raise ValueError("auto_score 必须是布尔值")
    normalized: dict[str, Any] = {"platform_order": order, "auto_score": bool(options.get("auto_score")), "platforms": {}}
    for platform in order:
        value = platforms.get(platform)
        if not isinstance(value, dict):
            raise ValueError(f"{platform} 平台配置无效")
        keywords = _clean_strings(value.get("keywords"))
        cities = _clean_strings(value.get("cities"))
        # 来源通道契约：非 BOSS 只允许 search；BOSS 通道需在白名单内；
        # 只选推荐页时允许空关键词/城市（依赖账号推荐流）。
        channels = list(value.get("source_channels") or ["search"]) if isinstance(value.get("source_channels"), list) else ["search"]
        channels = [str(channel).strip() for channel in channels if str(channel).strip()]
        if not channels:
            channels = ["search"]
        unknown_channels = [channel for channel in channels if channel not in SUPPORTED_BOSS_CHANNELS]
        if unknown_channels:
            raise ValueError(f"采集来源通道无效：{'、'.join(unknown_channels)}（只支持 搜索流/推荐页）")
        if platform != "boss" and "recommendation" in channels:
            raise ValueError(f"{platform} 平台当前不支持推荐页来源，推荐页仅对 BOSS 直聘开放")
        if "search" in channels:
            if not keywords:
                raise ValueError(f"{platform} 至少需要一个非空关键词")
            if not cities:
                raise ValueError(f"{platform} 至少需要一个城市")
        city_codes = value.get("city_codes") if isinstance(value.get("city_codes"), dict) else {}
        city_codes = {str(key).strip(): str(code).strip() for key, code in city_codes.items() if str(key).strip()}
        if platform == "zhilian":
            for city in cities:
                resolved = get_zhilian_city_code(city)
                if resolved:
                    # The platform snapshot is authoritative for known cities;
                    # this prevents a legacy BOSS code from crossing platforms.
                    city_codes[city] = resolved
            missing_cities = [city for city in cities if not city_codes.get(city)]
            if missing_cities:
                names = "、".join(missing_cities)
                raise ValueError(f"智联暂未内置城市编码：{names}；请换用采集窗口提供的城市名称，不能填写 BOSS 编码")
        if platform == "51job":
            for city in cities:
                resolved = get_51job_city_code(city)
                if resolved:
                    city_codes[city] = resolved
            missing_cities = [city for city in cities if not city_codes.get(city)]
            if missing_cities:
                names = "、".join(missing_cities)
                raise ValueError(f"51job 当前只开放已验证城市：{names} 尚未支持；不会猜测城市编码")
        try:
            max_pages = int(value.get("max_pages", 3))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{platform} 最大页数必须是整数") from exc
        if not 1 <= max_pages <= 10:
            raise ValueError(f"{platform} 最大页数范围为 1-10")
        sort = str(value.get("sort") or "default").strip()
        if sort not in SORT_OPTIONS[platform]:
            raise ValueError(f"{platform} 排序方式无效")
        recruitment_filter = str(value.get("recruitment_filter") or "").strip()
        if recruitment_filter not in {"", "campus", "experienced"}:
            raise ValueError("招聘类型过滤只支持：空（全部）、campus（只有实习）、experienced（只有正式岗）")
        recommendation_params: dict[str, int] = {}
        if platform == "boss":
            for field_name, low, high, label in (
                ("recommendation_max_scrolls", 1, 10, "推荐页最大分页轮次"),
                ("recommendation_max_cards", 1, 200, "推荐页最大卡片数"),
                ("recommendation_same_result_limit", 1, 5, "推荐页连续无新增轮次上限"),
            ):
                default = {"recommendation_max_scrolls": 4, "recommendation_max_cards": 50,
                           "recommendation_same_result_limit": 2}[field_name]
                try:
                    param = int(value.get(field_name, default))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{label}必须是整数") from exc
                if not low <= param <= high:
                    raise ValueError(f"{label}范围为 {low}-{high}")
                recommendation_params[field_name] = param
        normalized["platforms"][platform] = {
            "keywords": keywords,
            "cities": cities,
            "city_codes": city_codes,
            "max_pages": max_pages,
            "sort": sort,
            "recruitment_filter": recruitment_filter,
            "source_channels": channels,
        }
        if platform == "boss":
            normalized["platforms"][platform].update(recommendation_params)
    return normalized


class _SharedProcessor:
    def __init__(
        self,
        conn,
        request: PlatformCollectionRequest,
        *,
        run_id: str,
        platform_index: int,
        platform_total: int,
        stop_event: Event | None,
        config: dict[str, Any],
        emit: Callable[[CollectionProgress], None],
    ):
        self.conn = conn
        self.request = request
        self.run_id = run_id
        self.platform_index = platform_index
        self.platform_total = platform_total
        self.stop_event = stop_event
        self.config = config
        self.emit = emit
        self.progress = CollectionProgress(
            run_id=run_id, platform=request.platform, platform_index=platform_index,
            platform_total=platform_total, phase="queued", target=None,
            max_pages=request.max_pages,
        )
        self.new_job_ids: list[str] = []
        # 推荐页计划 Task 3：来源级进度（search / recommendation），供 progress.sources。
        self.source_progress: dict[str, dict[str, Any]] = {}

    def set_current_source(self, channel: str) -> None:
        entry = self.source_progress.setdefault(channel, {
            "label": COLLECTION_CHANNEL_LABELS.get(channel, channel),
            "status": "running",
            "seen": 0, "new": 0, "duplicate": 0, "filtered": 0, "parse_failed": 0,
        })
        entry["status"] = "running"
        self.event(current_source=channel)

    def finish_source(self, channel: str, *, status: str, reason_code: str = "") -> None:
        entry = self.source_progress.setdefault(channel, {
            "label": COLLECTION_CHANNEL_LABELS.get(channel, channel),
            "seen": 0, "new": 0, "duplicate": 0, "filtered": 0, "parse_failed": 0,
        })
        entry["status"] = status
        if reason_code:
            entry["reason_code"] = reason_code

    def _record_observation(self, candidate: JobCandidate) -> None:
        """来源观察：新旧岗位都记录；失败只降级，不影响采集结果。"""
        try:
            record_job_source_observation(
                self.conn,
                job_id=candidate.storage_id,
                source_platform=str(candidate.platform),
                source_channel=str(candidate.source_channel or "search"),
                source_keyword=str(candidate.source_keyword or ""),
                source_city=str(candidate.city or ""),
                source_city_code=str(candidate.city_code or ""),
                collection_run_id=str(self.run_id or ""),
            )
        except Exception:
            pass

    def event(self, *, phase: str | None = None, **values: Any) -> None:
        if phase:
            self.progress.phase = phase
        if values.pop("increment_filtered", False):
            self.progress.filtered += 1
        for key, value in values.items():
            if hasattr(self.progress, key):
                setattr(self.progress, key, value)
        self.emit(self.progress)

    def inspect(self, candidate: JobCandidate) -> bool:
        self.progress.seen += 1
        channel = str(candidate.source_channel or "search")
        if channel in self.source_progress:
            self.source_progress[channel]["seen"] += 1
        if job_identity_exists(
            self.conn,
            candidate.platform,
            candidate.source_job_id,
            legacy_job_id=candidate.storage_id,
        ):
            self.progress.duplicate += 1
            if channel in self.source_progress:
                self.source_progress[channel]["duplicate"] += 1
            # 重复命中也记录来源观察：推荐页命中已有搜索岗位时 UI 能看到双来源
            self._record_observation(candidate)
            self.event()
            return False
        profile = self.config.get("profile", {}) if isinstance(self.config.get("profile"), dict) else {}
        if matching_deal_breaker(candidate.title, profile.get("deal_breakers") or []):
            self.progress.filtered += 1
            self.event(message="职位名命中过滤规则")
            return False
        if matching_blocked_company(candidate.company, profile.get("blocked_companies") or []):
            self.progress.filtered += 1
            self.event(message="公司命中过滤规则")
            return False
        self.event()
        return True

    def save(self, candidate: JobCandidate) -> bool:
        profile = self.config.get("profile", {}) if isinstance(self.config.get("profile"), dict) else {}
        if matching_deal_breaker(candidate.jd, profile.get("jd_deal_breakers") or []):
            self.progress.filtered += 1
            self.event(message="JD 命中过滤规则")
            return True
        try:
            inserted = insert_job_if_new(self.conn, candidate.as_job_record())
        except Exception as exc:
            self.progress.save_failed += 1
            self.event(message=f"保存岗位失败：{type(exc).__name__}")
            return True
        if inserted:
            self.new_job_ids.append(candidate.storage_id)
            channel = str(candidate.source_channel or "search")
            if channel in self.source_progress:
                self.source_progress[channel]["new"] += 1
        else:
            self.progress.duplicate += 1
            channel = str(candidate.source_channel or "search")
            if channel in self.source_progress:
                self.source_progress[channel]["duplicate"] += 1
        self._record_observation(candidate)
        self.event(phase="saving")
        if self.stop_event is not None and self.stop_event.is_set():
            return False
        return self.stop_event is None or not self.stop_event.is_set()


class CollectionOrchestrator:
    """Run selected platform collectors one after another in the given order."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        db_path: Path | None = None,
        registry: CollectorRegistry | None = None,
        run_id: str | None = None,
        task_id: str = "",
    ):
        self.config = config
        self.db_path = db_path or Path("./data/openjob.db")
        self._uses_default_registry = registry is None
        self.registry = registry or CollectorRegistry({
            "boss": BossCollector,
            "zhilian": ZhilianCollector,
            "51job": Job51Collector,
        })
        self.run_id = run_id or str(uuid4())
        self.task_id = task_id
        self.stop_event = config.get("_workbench_stop_event")

    def run(self, raw_options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = normalize_collection_options(self.config, raw_options)
        order = options["platform_order"]
        states: dict[str, dict[str, Any]] = {
            platform: {"status": "queued", "new": 0, "target": None, "percent": None}
            for platform in order
        }
        create_collection_run(self.db_path, run_id=self.run_id, task_id=self.task_id, options=options, platform_states=states)
        all_new_ids: list[str] = []
        platform_results: list[PlatformCollectionResult] = []
        conn = get_db(self.db_path)
        try:
            for index, platform in enumerate(order, start=1):
                if self.stop_event is not None and self.stop_event.is_set():
                    states[platform]["status"] = "stopped"
                    states[platform]["reason_code"] = "user_stopped"
                    break
                raw = options["platforms"][platform]
                request = PlatformCollectionRequest(platform=platform, **raw)
                states[platform]["status"] = "running"
                self._persist(states, all_new_ids, platform)
                processor = _SharedProcessor(
                    conn, request, run_id=self.run_id, platform_index=index, platform_total=len(order),
                    stop_event=self.stop_event, config=self.config,
                    emit=lambda progress, p=platform, processor_ref=None: self._emit(
                        states, p, progress, all_new_ids, processor_ref.new_job_ids if processor_ref else []
                    ),
                )
                # Bind the processor into the callback after construction so the
                # current platform's progress is not confused with prior IDs.
                processor.emit = lambda progress, p=platform, processor_ref=processor: self._emit(
                    states, p, progress, all_new_ids, processor_ref.new_job_ids
                )
                hooks = CollectorHooks(
                    stop_event=self.stop_event,
                    on_list_candidate=processor.inspect,
                    on_candidate=processor.save,
                    on_parse_failed=lambda reason, p=processor: self._parse_failed(p, reason),
                    on_event=lambda p=processor, **kwargs: p.event(**kwargs),
                )
                # 推荐页计划 Task 3：为 BOSS 多来源初始化来源级进度
                request_channels = list(getattr(request, "source_channels", None) or ["search"])
                for channel in request_channels:
                    processor.set_current_source(channel)
                self._persist(states, all_new_ids, platform)
                try:
                    collector = (
                        BossCollector(config=self.config, safety_conn=conn)
                        if platform == "boss" and self._uses_default_registry
                        else self.registry.get(platform)
                    )
                    result = collector.collect(request, hooks)
                except CollectionError as exc:
                    result = PlatformCollectionResult(platform, "blocked", exc.code, exc.message, error=str(exc))
                except Exception as exc:
                    result = PlatformCollectionResult(platform, "failed", "network_error", f"{platform} 采集失败", error=str(exc)[:500])
                # 按来源标记各自真实状态（收尾 Batch A）：
                # 优先读 result.source_results 中该来源的阶段结果，
                # 避免 search 的完成状态被 recommendation 的 reason code 覆盖。
                for channel in request_channels:
                    source_result = (result.source_results or {}).get(channel)
                    if source_result:
                        source_status = str(source_result.get("status") or result.status)
                        source_reason = str(source_result.get("reason_code") or "")
                    else:
                        # 兼容智联/51job 与无来源结果的旧 collector
                        source_status = result.status
                        source_reason = result.reason_code if result.status != "completed" else ""
                    processor.finish_source(channel, status=source_status, reason_code=source_reason)
                result.new_job_ids = list(processor.new_job_ids)
                result.counts = self._counts(processor.progress)
                platform_results.append(result)
                states[platform].update({
                    "status": result.status,
                    "new": len(result.new_job_ids),
                    "percent": processor.progress.percent,
                    "seen": processor.progress.seen,
                    "duplicate": processor.progress.duplicate,
                    "filtered": processor.progress.filtered,
                    "parse_failed": processor.progress.parse_failed,
                    "save_failed": processor.progress.save_failed,
                    "keyword": processor.progress.keyword,
                    "city": processor.progress.city,
                    "page": processor.progress.page,
                    "max_pages": processor.progress.max_pages,
                    "reason_code": result.reason_code,
                    "message": result.message,
                    "current_source": getattr(processor.progress, "current_source", ""),
                    "sources": {name: dict(item) for name, item in processor.source_progress.items()},
                    "source_results": {name: dict(item) for name, item in (result.source_results or {}).items()},
                })
                all_new_ids.extend(result.new_job_ids)
                self._persist(states, all_new_ids, platform, stop_reason=result.reason_code, error=result.error)
                # A verification, rate-limit, or unknown blocking page is an
                # account-level signal. Stop the entire serial queue instead
                # of immediately moving the same browser session to another
                # recruitment platform.
                if (
                    result.status == "blocked"
                    or result.reason_code in {"user_stopped", "browser_disconnected"}
                    or (self.stop_event and self.stop_event.is_set())
                ):
                    break

        finally:
            conn.close()

        unique_new_ids = list(dict.fromkeys(str(job_id) for job_id in all_new_ids if str(job_id)))
        stopped = bool(self.stop_event and self.stop_event.is_set()) or any(r.reason_code == "user_stopped" for r in platform_results)
        errors = any(r.status in {"blocked", "failed"} for r in platform_results)
        shortages = any(r.status == "completed_with_shortage" for r in platform_results)
        outcome = "stopped" if stopped else "completed_with_errors" if errors else "completed_with_shortage" if shortages else "completed"
        if options["auto_score"] and unique_new_ids and not stopped:
            try:
                self._emit_scoring(states, unique_new_ids)
                from openjob.ai.scorer import score_jobs

                score_config = dict(self.config)
                score_config["_workbench_stop_event"] = self.stop_event
                score_jobs(score_config, scope="selected", job_ids=unique_new_ids, limit=None, force_rescore=False)
            except Exception as exc:
                outcome = "completed_with_errors"
                self._persist(states, unique_new_ids, "", error=f"自动评分失败：{str(exc)[:500]}")
        self._persist(states, unique_new_ids, "", status=outcome, stop_reason="user_stopped" if stopped else "")
        return {
            "run_id": self.run_id,
            "status": outcome,
            "platforms": states,
            "collected_job_ids": unique_new_ids,
            "results": [result.__dict__ for result in platform_results],
        }

    @staticmethod
    def _counts(progress: CollectionProgress) -> dict[str, int]:
        return {
            "seen": progress.seen, "new": progress.new, "duplicate": progress.duplicate,
            "filtered": progress.filtered, "parse_failed": progress.parse_failed, "save_failed": progress.save_failed,
        }

    def _parse_failed(self, processor: _SharedProcessor, reason: str) -> None:
        processor.progress.parse_failed += 1
        processor.event(phase="loading_detail", message=reason)

    def _emit(
        self,
        states: dict[str, dict[str, Any]],
        platform: str,
        progress: CollectionProgress,
        all_new_ids: list[str],
        platform_new_ids: list[str],
    ) -> None:
        progress.new = len(platform_new_ids) if progress.platform == platform else progress.new
        states[platform].update({
            "status": "running", "new": progress.new, "target": progress.target, "percent": progress.percent,
            "seen": progress.seen, "duplicate": progress.duplicate, "filtered": progress.filtered,
            "parse_failed": progress.parse_failed, "save_failed": progress.save_failed,
            "keyword": progress.keyword, "city": progress.city, "page": progress.page,
            "max_pages": progress.max_pages, "phase": progress.phase, "reason_code": progress.reason_code,
            "message": progress.message,
        })
        callback = self.config.get("_workbench_collect_progress")
        state = {
            **self._counts(progress), "progress": {
                "run_id": self.run_id, "outcome": "running", "current_platform": platform,
                "platform_index": progress.platform_index, "platform_total": progress.platform_total,
                "platforms": deepcopy(states),
            },
        }
        if callable(callback):
            callback(state)
        self._persist(states, [*all_new_ids, *platform_new_ids], platform)

    def _emit_scoring(self, states: dict[str, dict[str, Any]], new_ids: list[str]) -> None:
        callback = self.config.get("_workbench_collect_progress")
        if callable(callback):
            callback({
                "seen": sum(int(value.get("seen") or 0) for value in states.values()),
                "new": len(new_ids),
                "duplicate": sum(int(value.get("duplicate") or 0) for value in states.values()),
                "filtered": sum(int(value.get("filtered") or 0) for value in states.values()),
                "parse_failed": sum(int(value.get("parse_failed") or 0) for value in states.values()),
                "save_failed": sum(int(value.get("save_failed") or 0) for value in states.values()),
                "progress": {"run_id": self.run_id, "outcome": "scoring", "current_platform": "", "platforms": deepcopy(states)},
            })

    def _persist(
        self,
        states: dict[str, dict[str, Any]],
        new_ids: list[str],
        current_platform: str,
        *,
        status: str | None = None,
        stop_reason: str | None = None,
        error: str | None = None,
    ) -> None:
        update_collection_run(
            self.db_path, self.run_id, status=status, platform_states=states,
            collected_job_ids=list(dict.fromkeys(new_ids)), current_platform=current_platform,
            stop_reason=stop_reason, error=error,
        )
