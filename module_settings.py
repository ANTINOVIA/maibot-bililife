"""页面和后端共用的模块字段范围，防止局部保存覆盖其他模块。"""
MODULES = {
    "schedule": {"title":"每日安排", "description":"浏览、轻量整理与长休息相间的生活节奏", "fields":["planner_enabled","adaptive_schedule_enabled","max_dynamic_tasks","schedule_task_minutes","schedule_brief_minutes","schedule_gap_minutes","schedule_rest_minutes","schedule_rest_max_minutes","timezone","plan_time","active_start","active_end","task_timeout_seconds","max_retries"]},
    "browse": {"title":"浏览与理解", "description":"自主选题发现、每日浏览量与媒体处理", "fields":["browsing_enabled","max_videos","max_comment_sessions","max_comments_read","max_video_seconds","max_browse_seconds","allowed_topics","excluded_topics","allowed_uploaders","video_understanding_mode","max_frames","enable_asr","segment_seconds","max_download_mb","daily_download_mb"]},
    "comments": {"title":"视频评论", "description":"评论与回复风格提示词、避免表达、主人提醒与每日额度", "fields":["comments_enabled","auto_publish_comments","comment_prompt","comment_avoid_phrases","owner_uid","mention_owner","max_comments","max_per_video","cooldown_seconds"]},
    "replies": {"title":"回复跟进", "description":"回复量、检查频率与对话边界", "fields":["replies_enabled","auto_publish_replies","max_replies","max_thread_rounds","user_cooldown_seconds","reply_interval_seconds"]},
    "memory": {"title":"经历与记忆", "description":"保存经历、检索数量与上下文长度", "fields":["memory_enabled","recall_limit","recall_chars"]},
    "persona": {"title":"兴趣与人格干预", "description":"人工偏好、表达方式与兴趣变化", "fields":["evolution_enabled","persona_guidance","expression_preferences"]},
    "review": {"title":"每日复盘", "description":"复盘时间及指定会话推送", "fields":["review_enabled","review_time","summary_stream_id"]},
    "models": {"title":"模型与调用预算", "description":"每日调用量、Token 与超时", "fields":["max_model_calls","max_tokens","output_tokens","model_timeout_seconds","text_model_task","vision_model_task","asr_model_task"]},
    "resources": {"title":"存储与运行维护", "description":"存储、缓存及记录保留", "fields":["max_storage_mb","retention_days","cache_hours"]},
    "prompts": {"title":"运行提示词", "description":"九个阶段的管理员补充说明", "fields":["prompt_overrides"]},
}
