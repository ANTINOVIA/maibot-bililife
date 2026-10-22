from pathlib import Path
from typing import Any

import asyncio
import json
import re
import time

from pydantic import ValidationError

from .storage import dump, uid
from .model_router import ModelRouter


class ModelUnavailable(RuntimeError):
    pass


class MaiBotAdapter:
    def __init__(self, ctx, store, config, day):
        self.ctx, self.store, self.config, self.day = ctx, store, config, day
        self.prompts = json.loads((Path(__file__).parent / "prompts/runtime.json").read_text(encoding="utf-8"))
        self.check = lambda: None
        self.router = ModelRouter(ctx, store, config)

    async def persona(self):
        profile = await self.ctx.config.get("personality", {})
        if not isinstance(profile, dict):
            raise ModelUnavailable("宿主人格配置读取失败")
        return {**{key: str(profile.get(key, ""))[:3000] for key in ("personality", "reply_style")},
                "bililife_user_guidance":self.config().persona_guidance,
                "bililife_expression_preferences":self.config().expression_preferences}

    async def generate(self, stage, external, schema, images=None, media=None):
        cfg, day = self.config(), self.day()
        persona = await self.persona()
        system = (self.prompts["common"] + "\n" + self.prompts[stage] + "\n人格：" + dump(persona)
                  + "\n管理员补充：" + cfg.prompt_overrides.get(stage, "")
                  + "\n输出 JSON Schema：" + dump(schema.model_json_schema()))
        content = dump({"external_data": external})
        if len(content) > 35000:
            raise ValueError("模型输入超过分段预算")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
        if images:
            messages[1]["content"] = [{"type": "text", "text": content}] + [
                {"type": "image_url", "image_url": {"url": image}} for image in images]
        for attempt in range(2):
            self.check()
            # 中文按最多每字符一个 token 预留，并给图片保守预留；缺失 usage 时保留预扣。
            reserved = len(system) + len(content) + 1000 * len(images or []) + cfg.output_tokens + (int(media.get("seconds",90))*100 if media else 0)
            await self.store.call("reserve", day, {"model_calls": (1, cfg.max_model_calls), "tokens": (reserved, cfg.max_tokens)})
            started, call_id, outcome, actual_model, usage = time.monotonic(), uid(), "failed", "", {}
            try:
                self.router.check = self.check
                self.router.ctx = self.ctx
                try:
                    response = await asyncio.wait_for(self.router.generate(
                        messages, role=media["kind"] if media else "vision" if images else "text",
                        max_tokens=cfg.output_tokens, media=media), cfg.model_timeout_seconds)
                except RuntimeError as exc:
                    raise ModelUnavailable(str(exc)) from None
                if not isinstance(response, dict) or response.get("success") is False:
                    raise ModelUnavailable("宿主模型调用失败，请检查模型配置与服务状态")
                actual_model = str(response.get("model", response.get("model_name", "unknown")))[:150]
                usage = response.get("usage", response)
                usage = {k: v for k, v in usage.items() if k in ("prompt_tokens", "completion_tokens", "total_tokens") and type(v) is int and v >= 0}
                total = usage.get("total_tokens", usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))
                if total:
                    if total <= reserved:
                        await self.store.call("refund", day, "tokens", reserved - total)
                    else:
                        # 已发生的消耗必须入账，即使供应商报告超过预留。
                        await self.store.call("execute", "UPDATE budget SET used=used+? WHERE day=? AND resource='tokens'", (total - reserved, day))
                text = response.get("response", response.get("content", response.get("text", "")))
                if not isinstance(text, str) or not text.strip():
                    raise ModelUnavailable("模型未返回正文")
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
                try:
                    result = schema.model_validate_json(text, strict=True)
                except (ValueError, TypeError) as exc:
                    outcome = "invalid_schema"
                    errors = [{"field": ".".join(map(str, x["loc"])), "message": x["msg"]} for x in exc.errors(include_input=False)] if isinstance(exc, ValidationError) else [{"message": "返回值不是合法 JSON 对象"}]
                    await self.store.call("audit", "model_validation_error", {"stage": stage, "errors": errors})
                    if attempt == 0:
                        messages.append({"role": "user", "content": "上次返回无法通过 Schema，请按以下校验错误修复，仅输出合法 JSON：" + dump(errors)})
                        continue
                    raise ValueError("模型两次返回非法结构") from None
                outcome = "succeeded"
                return result
            except asyncio.TimeoutError:
                outcome = "timeout"
                raise ModelUnavailable("模型调用超时") from None
            finally:
                await self.store.call("execute", "INSERT INTO model_calls VALUES(?,?,?,?,?,?,?,?,?)",
                                      (call_id, day, stage, self.prompts["version"], actual_model, outcome, dump(usage), time.time(), time.monotonic() - started))

    async def transcribe(self, audio):
        cfg, day = self.config(), self.day()
        self.check()
        reserve = cfg.output_tokens + len(audio) // 640
        await self.store.call("reserve", day, {"model_calls": (1, cfg.max_model_calls), "tokens": (reserve, cfg.max_tokens)})
        started, outcome, model, usage = time.monotonic(), "failed", "", {}
        try:
            self.router.check, self.router.ctx = self.check, self.ctx
            response = await asyncio.wait_for(self.router.transcribe(audio), cfg.model_timeout_seconds)
            model = str(response.get("model", cfg.asr_model_task))
            text = response.get("text", response.get("content", ""))
            if response.get("success") is False or not isinstance(text, str) or not text.strip():
                raise ModelUnavailable("ASR 未返回可用转录")
            usage = {k: v for k, v in response.get("usage", {}).items() if k in ("total_tokens", "input_tokens", "output_tokens") and type(v) is int}
            total = usage.get("total_tokens")
            if total is not None and 0 < total <= reserve:
                await self.store.call("refund", day, "tokens", reserve-total)
            elif total and total > reserve:
                await self.store.call("execute", "UPDATE budget SET used=used+? WHERE day=? AND resource='tokens'", (total-reserve,day))
            outcome = "succeeded"
            return text[:7500]
        except RuntimeError as exc:
            raise ModelUnavailable(str(exc)) from None
        except asyncio.TimeoutError:
            outcome = "timeout"
            raise ModelUnavailable("ASR 模型调用超时") from None
        finally:
            await self.store.call("execute", "INSERT INTO model_calls VALUES(?,?,?,?,?,?,?,?,?)",
                                  (uid(),day,"asr",self.prompts["version"],model,outcome,dump(usage),time.time(),time.monotonic()-started))
