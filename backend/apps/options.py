"""模型和推理强度的枚举。

值写死，取自 DeepSeek 官方文档（https://api-docs.deepseek.com/api/create-response，2026-11-02 核对）：
  model             deepseek-flash、deepseek-v4-pro
  reasoning.effort  none（关闭思考）、low、high、max
真正的校验在 core 的 providers.py（MODELS / EFFORTS）；这里的枚举要和它保持一致（有测试盯着），
多出来的是给页面用的中文名和说明。官方改了可选值，两处一起改。
"""

from enum import Enum


class Model(str, Enum):
    FLASH = "deepseek-flash"
    PRO = "deepseek-v4-pro"

    @property
    def label(self) -> str:
        return {"deepseek-flash": "DeepSeek Flash", "deepseek-v4-pro": "DeepSeek V4 Pro"}[self.value]

    @property
    def hint(self) -> str:
        return {
            "deepseek-flash": "速度快、成本低，支持图片输入",
            "deepseek-v4-pro": "能力更强，适合复杂任务",
        }[self.value]


class Effort(str, Enum):
    """推理强度，按从弱到强排列（页面上的滑块就按这个顺序）。"""

    NONE = "none"
    LOW = "low"
    HIGH = "high"
    MAX = "max"

    @property
    def label(self) -> str:
        return {"none": "关闭", "low": "低", "high": "高", "max": "最高"}[self.value]

    @property
    def hint(self) -> str:
        return {
            "none": "不思考，直接回答",
            "low": "简单思考，速度较快",
            "high": "深入思考",
            "max": "最大强度，最慢也最贵",
        }[self.value]
