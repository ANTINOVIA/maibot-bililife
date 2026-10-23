from pathlib import Path

import asyncio
import base64
import shutil

from .maibot_adapter import ModelUnavailable


class MediaProcessor:
    def __init__(self, platform, host, config, cache, ffmpeg):
        self.platform, self.host, self.config, self.cache, self.ffmpeg = platform, host, config, cache, ffmpeg

    async def collect(self, metadata, evidence, video_id):
        cfg, images, direct = self.config(), [], None
        mode=cfg.video_understanding_mode
        evidence['understanding_mode']=mode
        required = mode!='subtitle' or cfg.enable_asr
        if not required or not shutil.which("ffmpeg"):
            evidence["limitations"].append("FFmpeg 不可用或媒体分析未启用")
            return images, direct
        role = mode if mode in ('video','audio') else ''
        provider = None
        if role:
            try:
                provider = await self.host.router.resolve(role)
            except RuntimeError as exc:
                raise ModelUnavailable(str(exc)) from None
            if provider is None:
                raise ModelUnavailable("当前 SDK 未提供原始音频/视频输入，请在模型提供商页配置")
        streams = await self.platform.streams(metadata)
        paths, downloaded, cached = [], 0, {}
        async def fetch(kind):
            nonlocal downloaded
            if kind in cached:
                return cached[kind]
            if not streams.get(kind):
                raise ValueError("没有可访问的" + kind + "媒体流")
            if downloaded >= cfg.max_download_mb * 1024**2:
                raise ValueError("同视频媒体下载总量已达上限")
            raw = await self.platform.download(streams[kind], cfg.max_download_mb * 1024**2 - downloaded)
            downloaded += len(raw)
            if downloaded > cfg.max_download_mb * 1024**2:
                raise ValueError("同视频媒体下载总量超过上限")
            path = self.cache / f"{video_id}.{kind}"
            await asyncio.to_thread(path.write_bytes, raw)
            paths.append(path)
            cached[kind] = path
            return path
        seconds = min(metadata["duration"],cfg.segment_seconds,120,cfg.max_video_seconds)
        label = f"{role}_segment_1@0-{seconds}s" if role else ""
        try:
            if role == "video":
                media = await fetch("video")
                clip = self.cache / f"{video_id}.clip.mp4"
                paths.append(clip)
                audio_included = bool(provider["video_has_audio"] and streams.get("audio"))
                args = ["-i",str(media)]
                if audio_included:
                    audio = await fetch("audio")
                    args += ["-i",str(audio),"-map","0:v:0","-map","1:a:0","-c:a","aac","-b:a","64k"]
                else:
                    args += ["-an"]
                args += ["-t",str(seconds),"-vf","scale=480:-2","-c:v","libx264","-preset","ultrafast","-b:v","280k",
                         "-maxrate","350k","-bufsize","700k","-threads","1","-movflags","+faststart","-y",str(clip)]
                await self.ffmpeg(args)
                content = await asyncio.to_thread(clip.read_bytes)
                if len(content)>8*1024**2:
                    raise ValueError("视频片段超过 8 MiB 模型上传上限")
                direct = {"kind":"video","mime":"video/mp4","bytes":content,"seconds":seconds}
                evidence["types"].append("direct_video")
                if audio_included:
                    evidence["types"].append("audio_analysis")
                evidence["media_segments"]=[{"from":0,"to":seconds,"label":label,"audio_included":audio_included}]
                evidence["coverage"]="直接视频片段理解（其余时段仅使用已取得字幕）"
                evidence["limitations"].append("只向模型提交开头受限片段，不是完整视频流；"+("片段含真实音轨" if audio_included else "片段不含音轨"))
            elif role == "audio":
                audio = await fetch("audio")
                segment = self.cache / f"{video_id}.segment.wav"
                paths.append(segment)
                await self.ffmpeg(["-i",str(audio),"-t",str(seconds),"-ac","1","-ar","16000","-threads","1","-y",str(segment)])
                direct={"kind":"audio","mime":"audio/wav","bytes":await asyncio.to_thread(segment.read_bytes),"seconds":seconds}
                evidence["types"].append("audio_analysis")
                evidence["media_segments"]=[{"from":0,"to":seconds,"label":label,"audio_included":True}]
                evidence["coverage"]="直接音频片段理解与可用字幕"
                evidence["limitations"].append("未向模型提交画面；音频仅覆盖开头受限片段")
            elif mode=='frames' and streams.get("video"):
                media=await fetch("video")
                positions=[metadata["duration"]*(i+1)/(cfg.max_frames+1) for i in range(cfg.max_frames)]
                for i,position in enumerate(positions):
                    self.platform.check()
                    frame=self.cache/f"{video_id}-{i}.jpg"
                    await self.ffmpeg(["-ss",str(round(position,2)),"-i",str(media),"-frames:v","1","-vf","scale=640:-2","-threads","1","-y",str(frame)])
                    content=await asyncio.to_thread(frame.read_bytes)
                    if len(content)>512000:
                        raise ValueError("关键帧超过大小上限")
                    images.append("data:image/jpeg;base64,"+base64.b64encode(content).decode())
                    evidence["frames"].append({"at":round(position,2),"label":f"frame_{i+1}@{round(position,2)}s"})
                if images:
                    evidence["types"].append("keyframes")
                    evidence["coverage"]="字幕/画面抽样理解" if evidence["segments"] else "关键帧抽样理解"
            if cfg.enable_asr and not evidence["segments"] and streams.get("audio"):
                audio=await fetch("audio")
                segment=self.cache/f"{video_id}.asr.wav"
                paths.append(segment)
                await self.ffmpeg(["-i",str(audio),"-t",str(seconds),"-ac","1","-ar","16000","-threads","1","-y",str(segment)])
                text=await self.host.transcribe(await asyncio.to_thread(segment.read_bytes))
                evidence["segments"]=[{"source":"asr","from":0,"to":seconds,"text":text}]
                evidence["types"].append("asr")
                if not direct:
                    evidence["coverage"]="开头音频转录与可用画面抽样"
            return images,direct
        finally:
            for path in paths:
                await asyncio.to_thread(path.unlink,missing_ok=True)
