"""火山方舟 Seedream 文生图适配器；密钥仅在服务端读取。"""
from __future__ import annotations

import base64
import io
import logging
from time import perf_counter

import requests
from PIL import Image, UnidentifiedImageError

from backend.app.core.config import get_settings

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class SeedreamConfigurationError(RuntimeError):
    """图片模型缺少服务端配置。"""


class SeedreamGenerationError(RuntimeError):
    """图片服务请求或返回内容无效。"""


class SeedreamImageClient:
    """一次请求生成一张竖版视觉图，返回已解码的 RGB 图像。"""

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.ark_api_key
        self.url = settings.ark_image_url
        self.model = settings.ark_image_model
        self.speed = settings.ark_image_speed
        self.size = settings.ark_image_size

    def generate(self, visual_prompt: str, index: int) -> Image.Image:
        if not self.api_key:
            raise SeedreamConfigurationError("请先在项目 .env 中配置 ARK_API_KEY，并重启后端")
        palette = (
            "奶油白、深海蓝、珊瑚橙",
            "暖米色、鼠尾草绿、深蓝灰",
            "浅杏色、雾霾蓝、赭红",
            "柔和象牙白、墨绿、金橙",
            "浅沙色、石板蓝、砖红",
        )[index % 5]
        prompt = (
            f"主题视觉：{visual_prompt.strip()[:110]}。"
            "为中文生活方式平台的竖版知识图文创作一张纯视觉插画。"
            "当代编辑设计风格，精致纸艺与柔和三维材质结合，形象具体、构图清晰、有视觉焦点，"
            f"配色以{palette}为主。主体放在画面中央偏上，四周保留呼吸空间，3:4竖版。"
            "画面中不要出现任何文字、数字、字母、标志、边框、水印或伪文字。"
        )
        started = perf_counter()
        try:
            response = requests.post(
                self.url,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "size": self.size,
                    "optimize_prompt_options": {"mode": self.speed},
                    "response_format": "b64_json",
                    "output_format": "jpeg",
                    "watermark": False,
                },
                timeout=(10, 180),
            )
        except requests.Timeout as exc:
            logger.warning("Seedream timeout model=%s mode=%s elapsed=%.2fs", self.model, self.speed, perf_counter() - started)
            raise SeedreamGenerationError("Seedream 生图超时，请稍后重试") from exc
        except requests.RequestException as exc:
            raise SeedreamGenerationError("无法连接 Seedream 图片服务，请检查网络和接口地址") from exc
        if not response.ok:
            logger.warning("Seedream failed status=%s model=%s mode=%s elapsed=%.2fs", response.status_code, self.model, self.speed, perf_counter() - started)
            if response.status_code in (401, 403):
                message = "Seedream 鉴权失败，请检查 ARK_API_KEY 和模型权限"
            elif response.status_code == 429:
                message = "Seedream 请求过于频繁或额度不足，请稍后重试"
            else:
                message = f"Seedream 图片接口返回 HTTP {response.status_code}，请检查模型配置"
            raise SeedreamGenerationError(message)
        try:
            encoded = response.json()["data"][0]["b64_json"]
            raw = base64.b64decode(encoded, validate=True)
            with Image.open(io.BytesIO(raw)) as source:
                if source.width * source.height > 20_000_000:
                    raise ValueError("图像尺寸过大")
                result = source.convert("RGB")
                logger.info("Seedream complete model=%s mode=%s size=%s elapsed=%.2fs", self.model, self.speed, result.size, perf_counter() - started)
                return result
        except (KeyError, IndexError, TypeError, ValueError, UnidentifiedImageError) as exc:
            raise SeedreamGenerationError("Seedream 没有返回可用的图片") from exc
