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
        for index, segment in enumerate(evidence["segments"], 1):
            segment["source_id"] = f"subtitle_{index}"
        await self.store.call("execute", "UPDATE videos SET evidence=?,state='analyzing' WHERE id=?", (dump(evidence), record_id))
        references = evidence_references(metadata, evidence)
        external = {"metadata": metadata, "evidence": evidence,
                    "citation_guide": {
                        "media_labels": references["media_labels"],
                        "metadata_sources": [{"id": key, "text": value[:180]} for key, value in
                                             references["by_id"].items() if key.startswith("metadata:")],
                        "text_examples": [value[:180] for value in references["texts"][:6]],
                        "rule": "优先在 evidence_id 填支持此细节的来源编号：字幕的 source_id、元数据 id、或一个完整媒体 label；evidence_quote 可以留空，由程序取该来源原文。若填写 evidence_quote，必须与选中来源相符。不要在引用内拼接说明或概括。图片按 frames 顺序对应；视频/音频使用 media_segments 的 label。观察描述放 text。无来源的细节删除。"}}
        for attempt in range(2):
            analysis = await self.host.generate("understand", external, Understanding, images, media=direct_media)
            try:
                guard_unobserved_senses(analysis.feeling, evidence)
            except ValueError as exc:
                if attempt:
                    raise
                external["validation_feedback"] = str(exc)
                continue
            invalid = []
            for i, detail in enumerate(analysis.details):
                quote = resolve_citation(detail.evidence_quote, detail.evidence_id, references)
                if quote is None:
                    invalid.append(i)
                else:
                    detail.evidence_quote = quote
            if not invalid:
                break
            await self.store.call("audit", "evidence_validation_error", {
                "video_id": record_id, "invalid_details": invalid, "attempt": attempt + 1,
                "prompt_version": self.host.prompts.get("version", "unknown") if hasattr(self.host, "prompts") else "unknown",
                "rejected_citations": [{"index": i, "evidence_id": analysis.details[i].evidence_id,
                                        "quote": analysis.details[i].evidence_quote} for i in invalid]})
            if attempt:
                raise ValueError("理解结果两次未提供可核对的原始证据引用")
            external["validation_feedback"] = {
                "invalid_details": [{"index": i, "text": analysis.details[i].text,
                                     "rejected_quote": analysis.details[i].evidence_quote} for i in invalid],
                "previous_result": analysis.model_dump(),
                "instruction": "重新输出完整结果，保留已通过的引用，仅修复失败项。优先填写资料中已存在的 evidence_id，并让 evidence_quote 为空字符串，程序将填入对应原文；字幕编号在 segments[].source_id。不要猜编号，选定来源须确实支持 text。也可以删除无依据项。引用不是自己的概括、OCR 或听写；标签只定位证据，不证明推断。"}
        visual_available = bool(images or (direct_media and direct_media["kind"] == "video"))
        if evidence["types"] == ["metadata"] or (analysis.visual_required and not visual_available):
            analysis.suitable_for_comment = False
            analysis.reason = "元数据或缺失重要视觉证据，不生成发布草稿"
        result = analysis.model_dump()
        result["context_sufficient"] = bool(evidence["types"] != ["metadata"] and (not analysis.visual_required or visual_available))
        return evidence, result


def evidence_references(metadata, evidence):
    runs, current, previous_end = [], [], None
    for segment in evidence.get("segments", []):
        start, end = segment.get("from"), segment.get("to")
        adjacent = (isinstance(start, (int, float)) and isinstance(previous_end, (int, float))
                    and 0 <= start - previous_end <= 2)
        if current and not adjacent:
            runs.append("\n".join(current))
            current = []
        current.append(segment["text"])
        previous_end = end
    if current:
        runs.append("\n".join(current))
    by_id = {"metadata:title": metadata.get("title", ""),
             "metadata:description": metadata.get("description", "")}
    by_id.update({f"subtitle_{i}": x["text"] for i, x in enumerate(evidence.get("segments", []), 1)})
    labels = [x["label"] for x in evidence.get("frames", [])] + [x["label"] for x in evidence.get("media_segments", [])]
    by_id.update({label: label for label in labels})
    by_id = {key: value for key, value in by_id.items() if value.strip()}
    return {
        "by_id": by_id,
        "texts": [value for value in [metadata.get("title", ""), metadata.get("description", ""),
                  *[x["text"] for x in evidence.get("segments", [])],
                  *runs] if value.strip()],
        "media_labels": labels,
    }


def resolve_citation(raw_quote, source_id, references):
    """Resolve only known sources; whitespace differences preserve the original text."""
    quote = raw_quote.strip().strip('`“”「」"').strip()
    if source_id:
        source = references["by_id"].get(source_id)
        if not source:
            return None
        if not quote:
            # Bound copied evidence to the existing quote field, including long descriptions.
            return source[:180]
        sources = [source]
    else:
        if quote in references["media_labels"]:
            return quote
        sources = references["texts"]
    if not quote:
        return None
    for source in sources:
        if quote in source:
            return quote
        compact, positions = [], []
        for index, char in enumerate(source):
            if not char.isspace():
                compact.append(char)
                positions.append(index)
        needle = ''.join(char for char in quote if not char.isspace())
        if not needle:
            continue
        start = ''.join(compact).find(needle)
        if start >= 0:
            original = source[positions[start]:positions[start + len(needle) - 1] + 1]
            if len(original) <= 250:
                return original
    return None


def evidence_text(metadata, evidence):
    return "\n".join([metadata.get("title", ""), metadata.get("description", ""),
                      *[x["text"] for x in evidence.get("segments", [])],
                      *[x["label"] for x in evidence.get("frames", [])],
                      *[x["label"] for x in evidence.get("media_segments", [])]])
