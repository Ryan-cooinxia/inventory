"""
视觉工具模型服务 (Vision Tool Model Service)

负责调用视觉模型 API 对商品图片执行：
- SKU 图片识别（颜色、款式、配件差异）
- 详情图 OCR（提取文字、参数、尺寸）
- 图片合规检查（中文、水印、二维码、Logo、价格）
- 商品事实补充（从图片中提取可证实的事实）

架构：
    图片 → Vision Tool Model → 结构化 JSON → DeepSeek 主模型 → 商品事实库

当前状态：待实现。需用户配置 VisionModelConfig 并选择 provider 后接实际 API。
支持的 provider：openai_vision / qwen_vl / gemini_vision / custom_http
"""
import json
import datetime
from typing import Optional

from models import (
    db, OzonSourceMedia, VisionModelConfig,
    ImageAnalysisJob, ImageFact,
)


def get_active_config(user) -> Optional[VisionModelConfig]:
    """获取用户当前启用的视觉模型配置"""
    return (VisionModelConfig
            .select()
            .where((VisionModelConfig.user == user) &
                   (VisionModelConfig.enabled == True))
            .first())


def analyze_image(media: OzonSourceMedia, task_type: str, user) -> Optional[dict]:
    """
    对单张图片执行视觉识别。

    Args:
        media: OzonSourceMedia 记录
        task_type: sku_image / detail_ocr / compliance_check / fact_extraction
        user: 当前用户

    Returns:
        归一化后的 vision_result JSON dict，失败返回 None
    """
    config = get_active_config(user)
    if not config:
        return None

    # TODO: 根据 config.provider 调用对应的视觉模型 API
    # 1. 构建请求（图片 base64 + prompt + schema 约束）
    # 2. 调用 API
    # 3. 解析响应 → 归一化为 vision_result schema
    # 4. 创建 ImageAnalysisJob 记录
    # 5. 根据识别结果创建 ImageFact 记录
    # 6. 返回 parsed_json

    # 占位实现：记录任务但不实际调用
    job = ImageAnalysisJob.create(
        user=user,
        media=media,
        source=media.source,
        task_type=task_type,
        provider=config.provider,
        model_name=config.model_name,
        status='pending',
    )
    return None


def analyze_batch(media_list: list, task_type: str, user) -> list:
    """
    批量分析多张图片。

    Args:
        media_list: OzonSourceMedia 记录列表
        task_type: 任务类型
        user: 当前用户

    Returns:
        成功的结果列表（失败项为 None）
    """
    config = get_active_config(user)
    if not config:
        return [None] * len(media_list)

    max_batch = config.max_images_per_batch or 5
    results = []
    for i in range(0, len(media_list), max_batch):
        batch = media_list[i:i + max_batch]
        for media in batch:
            result = analyze_image(media, task_type, user)
            results.append(result)
    return results


def normalize_vision_response(response: dict) -> dict:
    """
    将不同 provider 的原始响应归一化为统一的 vision_result schema。

    统一格式参见 docs/ozon_vision_tool_model_plan.md §5
    """
    return {
        "schema_version": "1.0",
        "task_type": response.get("task_type", ""),
        "image": response.get("image", {}),
        "detected": response.get("detected", {}),
        "compliance": response.get("compliance", {}),
        "facts": response.get("facts", []),
        "uncertain": response.get("uncertain", []),
        "summary_cn": response.get("summary_cn", ""),
        "model": response.get("model", {}),
    }


def check_image_compliance(media: OzonSourceMedia, user) -> dict:
    """
    快速合规检查（不记录 Job，返回简单结果）。

    Returns:
        {'has_chinese': bool, 'has_watermark': bool, 'has_qr_code': bool,
         'has_price': bool, 'has_platform_logo': bool, 'ozon_ready': bool}
    """
    # TODO: 实际调用视觉模型
    return {
        'has_chinese': False,
        'has_watermark': False,
        'has_qr_code': False,
        'has_price': False,
        'has_platform_logo': False,
        'ozon_ready': True,
    }
