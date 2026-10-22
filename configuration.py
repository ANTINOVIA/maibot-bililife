from datetime import time
from typing import Dict, List, Literal
from zoneinfo import ZoneInfo

from maibot_sdk import Field, PluginConfigBase
from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    timezone: str = "Asia/Shanghai"
    plan_time: str = "09:00"
    review_time: str = "22:00"
    active_start: str = "09:00"
    active_end: str = "22:30"
    planner_enabled: bool = True
    browsing_enabled: bool = True
    comments_enabled: bool = True
    replies_enabled: bool = True
    auto_publish_comments: bool = False
    auto_publish_replies: bool = False
    review_enabled: bool = True
    persona_guidance: str = ""
    expression_preferences: List[str] = []
    allowed_topics: List[str] = ["科学", "技术", "日常生活"]
    excluded_topics: List[str] = []
    allowed_uploaders: List[str] = []
    allowed_videos: List[str] = []
    max_videos: int = 4
    max_video_seconds: int = 900
    max_browse_seconds: int = 2400
    max_comments: int = 1
    max_replies: int = 2
    max_per_video: int = 1
    max_thread_rounds: int = 2
    cooldown_seconds: int = 3600
    user_cooldown_seconds: int = 21600
    reply_interval_seconds: int = 3600
    max_model_calls: int = 24
    max_tokens: int = 70000
    output_tokens: int = 1600
    max_download_mb: int = 40
    daily_download_mb: int = 120
    max_storage_mb: int = 256
    max_frames: int = 3
    segment_seconds: int = 90
    retention_days: int = 60
    cache_hours: int = 12
    concurrency: Literal[1] = 1
    task_timeout_seconds: int = 300
    model_timeout_seconds: int = 100
    max_retries: int = 2
    text_model_task: str = "utils"
    vision_model_task: str = "vlm"
    asr_model_task: str = "voice"
    enable_asr: bool = False
    enable_frames: bool = True
    enable_audio_model: bool = False
    enable_video_model: bool = False
    memory_enabled: bool = True
    evolution_enabled: bool = True
    recall_limit: int = 4
    recall_chars: int = 2400
    summary_stream_id: str = ""
    prompt_overrides: Dict[str, str] = {}

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (KeyError,ValueError):
            raise ValueError("未知业务时区，请使用 Asia/Shanghai 等 IANA 时区名称") from None
        return value

    @field_validator("plan_time", "review_time", "active_start", "active_end")
    @classmethod
    def valid_time(cls, value):
        if len(value) != 5:
            raise ValueError("时间须为 HH:MM")
        time.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def bounded(self):
        bounds = {"max_videos": (0, 20), "max_video_seconds": (30, 7200), "max_browse_seconds": (60, 14400),
                  "max_comments": (0, 10), "max_replies": (0, 20), "max_per_video": (1, 3),
                  "max_thread_rounds": (1, 5), "cooldown_seconds": (60, 86400),
                  "user_cooldown_seconds": (60, 604800), "reply_interval_seconds": (900, 86400),
                  "max_model_calls": (0, 200), "max_tokens": (0, 1000000), "output_tokens": (256, 4096),
                  "max_download_mb": (1, 200), "daily_download_mb": (1, 1000), "max_storage_mb": (32, 2048),
                  "max_frames": (0, 8), "segment_seconds": (15, 300), "retention_days": (7, 365),
                  "cache_hours": (1, 168), "task_timeout_seconds": (30, 1800), "model_timeout_seconds": (10, 300),
                  "max_retries": (0, 4), "recall_limit": (1, 8), "recall_chars": (200, 6000)}
        for key, (low, high) in bounds.items():
            if not low <= getattr(self, key) <= high:
                raise ValueError(f"{key} 必须在 {low}..{high} 之间")
        for rows in (self.allowed_topics, self.excluded_topics, self.allowed_uploaders):
            if len(rows) > 30 or any(not x.strip() or len(x) > 80 for x in rows):
                raise ValueError("主题或 UP 主列表过长或含空项")
        if any(not x.isdigit() for x in self.allowed_uploaders):
            raise ValueError("UP 主限制必须填写数字 UID")
        import re
        if len(self.allowed_videos) > 30 or any(not re.fullmatch(r"BV[0-9A-Za-z]{10}", x) for x in self.allowed_videos):
            raise ValueError("指定视频范围须为最多 30 个合法 BV 号")
        if len(self.persona_guidance) > 2000 or len(self.expression_preferences)>12 or any(len(x)>200 for x in self.expression_preferences):
            raise ValueError("人格干预说明或表达偏好过长")
        if not self.comments_enabled:
            self.auto_publish_comments = False
        if not self.replies_enabled:
            self.auto_publish_replies = False
        if (self.auto_publish_comments or self.auto_publish_replies) and not (self.allowed_topics or self.allowed_uploaders or self.allowed_videos):
            raise ValueError("自动发布需要至少设置主题、UP 主或指定视频范围")
        if len(self.prompt_overrides) > 9 or any(len(v) > 2000 for v in self.prompt_overrides.values()):
            raise ValueError("提示词补充过长")
        if set(self.prompt_overrides) - {"plan", "select", "understand", "decide", "comment", "reply", "memory", "evolution", "review"}:
            raise ValueError("提示词补充含未知阶段")
        return self


class PluginSection(PluginConfigBase):
    enabled: bool = Field(default=True, description="启用插件；各模块的开关与预算在 BiliLife 页面管理")
    config_version: str = Field(default="0.1.0", description="插件配置格式版本，由插件维护")


class PluginConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)


def migrate_settings(data):
    """0.2.0 模式仅在加载旧配置时转换；新 API 和配置不再接受模式字段。"""
    result = dict(data)
    legacy = result.pop("mode", None)
    confirmed = result.pop("autonomous_limits_confirmed", False)
    if legacy is not None:
        for key in ("comments_enabled", "replies_enabled"):
            result.setdefault(key, legacy != "observe_only")
        for key in ("auto_publish_comments", "auto_publish_replies"):
            result.setdefault(key, legacy == "autonomous" and confirmed is True)
    return result
