"""persist_collection_preferences: 键位与读取权威位一致 + 顶层镜像防 CLI 漂移。

2026-09-26 审计 R2：旧回写把 source_channels/recommendation_* 塞进
platforms.boss.search（被 platforms.boss 顶层静默遮蔽成死配置），且不镜像
顶层 search（CLI 只读顶层 → max_pages/sort/recruitment_filter 实际漂移）。
"""

from copy import deepcopy

from openjob.collection.orchestrator import (
    normalize_collection_options,
    persist_collection_preferences,
)

BASE_CONFIG = {
    "search": {"keywords": ["旧词"], "cities": ["广州"], "max_pages": 3, "sort": "default"},
    "collection": {"default_order": ["boss"], "auto_score_default": True},
    "platforms": {
        "boss": {
            "enabled": True,
            "source_channels": ["search"],
            "recommendation_max_scrolls": 4,
            "recommendation_max_cards": 50,
            "search": {
                "keywords": ["数据分析实习"],
                "cities": ["广州", "佛山"],
                "max_pages": 1,
                "sort": "newest",
                "source_channels": ["search", "recommendation"],
                "recommendation_max_scrolls": 1,
                "recommendation_max_cards": 20,
            },
        },
        "zhilian": {"enabled": False},
    },
    "profile": {"target_cities": ["广州"]},
}

OPTIONS = {
    "platform_order": ["boss"],
    "auto_score": True,
    "platforms": {
        "boss": {
            "keywords": ["数据分析实习", "AI 产品实习"],
            "cities": ["广州", "佛山"],
            "city_codes": {},
            "max_pages": 1,
            "sort": "newest",
            "recruitment_filter": "campus",
            "source_channels": ["search", "recommendation"],
            "recommendation_max_scrolls": 1,
            "recommendation_max_cards": 20,
            "recommendation_same_result_limit": 2,
        }
    },
}


class TestPersistCollectionPreferences:
    def test_channel_keys_landed_on_platform_top_level(self):
        config = deepcopy(BASE_CONFIG)
        persist_collection_preferences(config, OPTIONS)
        boss = config["platforms"]["boss"]
        assert boss["source_channels"] == ["search", "recommendation"]
        assert boss["recommendation_max_scrolls"] == 1
        assert boss["recommendation_max_cards"] == 20
        assert "source_channels" not in boss["search"]
        assert "recommendation_max_scrolls" not in boss["search"]

    def test_search_keys_landed_in_platform_search(self):
        config = deepcopy(BASE_CONFIG)
        persist_collection_preferences(config, OPTIONS)
        search = config["platforms"]["boss"]["search"]
        assert search["keywords"] == ["数据分析实习", "AI 产品实习"]
        assert search["max_pages"] == 1
        assert search["sort"] == "newest"
        assert search["recruitment_filter"] == "campus"

    def test_top_level_search_mirrored_for_cli(self):
        config = deepcopy(BASE_CONFIG)
        persist_collection_preferences(config, OPTIONS)
        top = config["search"]
        assert top["keywords"] == ["数据分析实习", "AI 产品实习"]
        assert top["max_pages"] == 1
        assert top["sort"] == "newest"
        assert top["recruitment_filter"] == "campus"
        assert top["cities"] == ["广州", "佛山"]

    def test_platform_top_level_channel_keys_survive_rewrite(self):
        """回写不能覆盖 platform 顶层已有的其他键（enabled 等）。"""
        config = deepcopy(BASE_CONFIG)
        persist_collection_preferences(config, OPTIONS)
        assert config["platforms"]["boss"]["enabled"] is True

    def test_unselected_platforms_disabled(self):
        config = deepcopy(BASE_CONFIG)
        persist_collection_preferences(config, OPTIONS)
        assert config["platforms"]["zhilian"]["enabled"] is False

    def test_roundtrip_normalize_reads_back_same_channels(self):
        """闭环：persist 后再 normalize，source_channels 必须与 options 一致。"""
        config = deepcopy(BASE_CONFIG)
        persist_collection_preferences(config, OPTIONS)
        options = normalize_collection_options(config, None)
        assert options["platforms"]["boss"]["source_channels"] == ["search", "recommendation"]
        assert options["platforms"]["boss"]["recommendation_max_scrolls"] == 1
        assert options["platforms"]["boss"]["max_pages"] == 1
        assert options["platforms"]["boss"]["sort"] == "newest"
        assert options["platforms"]["boss"]["recruitment_filter"] == "campus"

    def test_legacy_empty_slots_filled_from_mirror(self):
        """platforms.boss.search 若缺键，顶层镜像值经 legacy 填空逻辑兜底。"""
        config = deepcopy(BASE_CONFIG)
        del config["platforms"]["boss"]["search"]["max_pages"]
        persist_collection_preferences(config, OPTIONS)
        options = normalize_collection_options(config, None)
        assert options["platforms"]["boss"]["max_pages"] == 1
