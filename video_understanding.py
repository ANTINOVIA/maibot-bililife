from pathlib import Path

import asyncio
import base64
import json
import shutil

from bilibili_api.exceptions import CredentialNoSessdataException

from .schemas import Understanding
from .media_processor import MediaProcessor
from .security import guard_unobserved_senses
from .storage import dump
from .vendor.client import PlatformError, VideoReadError


class VideoUnderstanding:
    def __init__(self, platform, host, store, config, cache):
        self.platform, self.host, self.store, self.config, self.cache = platform, host, store, config, cache
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)

    async def ffmpeg(self, arguments):
        process = await asyncio.create_subprocess_exec(shutil.which("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", *arguments,
                                                       stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(process.wait(), 45)
            if process.returncode:
                raise RuntimeError("媒体解码失败")
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def analyze(self, metadata, video_id):
        record_id, video_id = video_id, video_id.replace(":", "-")
        cfg, images = self.config(), []
        evidence = {"types": ["metadata"], "segments": [], "frames": [], "limitations": [], "coverage": "元数据浏览", "complete": False}
        try:
            evidence["segments"], note = await self.platform.subtitles(metadata)
            evidence["limitations"].append(note)
            if evidence["segments"]:
                evidence["types"].append("subtitles")
                evidence["coverage"] = "部分字幕理解"
        except (PlatformError, VideoReadError, CredentialNoSessdataException, ValueError, RuntimeError) as exc:
            # 平台明确登录/限制状态必须交给执行器暂停，不用视觉读取规避。
            if isinstance(exc, PlatformError) and exc.code in (-101, -111, -352, -412, 412):
                raise
            evidence["limitations"].append("字幕不可用：" + type(exc).__name__)
        processor = MediaProcessor(self.platform, self.host, self.config, self.cache, self.ffmpeg)
        images, direct_media = await processor.collect(metadata, evidence, video_id)
        await self.store.call("execute", "UPDATE videos SET evidence=?,state='analyzing' WHERE id=?", (dump(evidence), record_id))
        external = {"metadata": metadata, "evidence": evidence}
        text = evidence_text(metadata, evidence)
        for attempt in range(2):
            analysis = await self.host.generate("understand", external, Understanding, images, media=direct_media)
            try:
                guard_unobserved_senses(analysis.feeling, evidence)
            except ValueError as exc:
                if attempt:
                    raise
                external["validation_feedback"] = str(exc)
                continue
            invalid = [i for i, detail in enumerate(analysis.details) if not detail.evidence_quote or detail.evidence_quote not in text]
            if not invalid:
                break
            await self.store.call("audit", "evidence_validation_error", {"video_id": record_id, "invalid_details": invalid})
            if attempt:
                raise ValueError("理解结果两次未提供可核对的原始证据引用")
            external["validation_feedback"] = "以下 details 条目的 evidence_quote 不在资料内：" + str(invalid) + "。逐字复制字幕片段或元数据短句；画面只引用给定 frame_N@位置s 标签，不引用画面 OCR 或自己写的描述。必要时删除无依据条目。"
        visual_available = bool(images or (direct_media and direct_media["kind"] == "video"))
        if evidence["types"] == ["metadata"] or (analysis.visual_required and not visual_available):
            analysis.suitable_for_comment = False
            analysis.reason = "元数据或缺失重要视觉证据，不生成发布草稿"
        result = analysis.model_dump()
        result["context_sufficient"] = bool(evidence["types"] != ["metadata"] and (not analysis.visual_required or visual_available))
        return evidence, result


def evidence_text(metadata, evidence):
    return "\n".join([metadata.get("title", ""), metadata.get("description", ""),
                      *[x["text"] for x in evidence.get("segments", [])],
                      *[x["label"] for x in evidence.get("frames", [])],
                      *[x["label"] for x in evidence.get("media_segments", [])]])
