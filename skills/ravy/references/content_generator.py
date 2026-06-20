"""
Ravy 爆款素材创作工具
=======================
短视频脚本 · 分镜头脚本 · 广告标题 · 落地页文案 · 私信话术 · 批量多版本

依赖安装:
  pip install pyyaml

可选依赖（增强功能）:
  pip install openai                    # LLM 文案生成
  pip install langchain                 # 工作流编排
  pip install jinja2                    # 模板渲染

运行方式:
  python content_generator.py --mode script       # 生成短视频脚本
  python content_generator.py --mode headline      # 生成标题
  python content_generator.py --mode landing       # 生成落地页文案
  python content_generator.py --mode dm            # 生成私信话术
  python content_generator.py --mode batch         # 批量全流程
"""

import json
import sys
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Dict, Optional
import re

# Windows GBK 兼容
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", errors="replace", closefd=False)
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = open(sys.stderr.fileno(), "w", encoding="utf-8", errors="replace", closefd=False)

# ============================================================
# 数据类型
# ============================================================

@dataclass
class ProductInfo:
    name: str
    category: str
    price: str
    selling_points: List[str]
    differentiators: List[str]
    target_audience: str
    pain_points: List[str]

@dataclass
class CopyVariant:
    version: str
    angle: str
    structure: str
    tone: str
    hook: str
    body: str
    cta: str

@dataclass
class ShortVideoScript:
    scene_id: str
    duration: str
    visual: str
    voiceover: str
    sound_effect: str
    subtitle: str

@dataclass
class ScriptOutput:
    title: str
    product: str
    platform: str
    hook: str
    variants: List[CopyVariant]
    storyboard: List[ShortVideoScript] = field(default_factory=list)
    compliance_check: Dict = field(default_factory=dict)

@dataclass
class BatchOutput:
    batch_id: str
    product: str
    platform: str
    outputs: List[ScriptOutput] = field(default_factory=list)
    generated_at: str = ""


# ============================================================
# 模板库
# ============================================================

HOOK_TEMPLATES = {
    "result_first": [
        "XX天学会{skill}，我是怎么做到的",
        "从{from_state}到{to_state}，我只做对了一件事",
        "{age}岁的我，终于实现了{dream}",
    ],
    "pain_point": [
        "你是不是也遇到了{problem}的问题？",
        "{pain_point}的姐妹/兄弟，这条一定要看",
        "别划走，{problem}有救了",
    ],
    "counter_intuitive": [
        "90%的人都不知道的{truth}真相",
        "{industry}的商家不会告诉你的秘密",
        "停！{behavior}千万不要这样做了",
    ],
    "suspense": [
        "做完这3件事，我的{outcome}发生了剧变",
        "{industry}行业的黑幕，我今天必须说",
        "这条视频可能会被删，先保存",
    ],
    "circle_talk": [
        "只有{industry}行业的人才懂的内幕",
        "做{business}的老板，请务必看完",
        "给想从事{career}的人几句忠告",
    ],
}

STRUCTURE_TEMPLATES = {
    "problem_solution": {
        "name": "问题→方案→结果",
        "steps": ["抛出痛点问题", "引入产品/方案", "展示使用结果", "CTA引导"],
    },
    "story_turn": {
        "name": "故事→转折→推荐",
        "steps": ["个人经历引入", "遇到问题转折", "发现解决方案", "推荐+CTA"],
    },
    "data_compare": {
        "name": "数据→对比→结论",
        "steps": ["抛出数据冲击", "对比前后差异", "得出结论/推荐", "CTA"],
    },
    "authority": {
        "name": "权威→原理→产品",
        "steps": ["建立专业身份", "解释核心原理", "引出产品方案", "CTA"],
    },
}

PLATFORM_STYLES = {
    "抖音": {
        "tone": "快节奏/口语化/情绪强",
        "hook_time": "前3秒",
        "speech_speed": "180-260字/分钟",
        "title_max": 30,
        "tags": "2-5个",
    },
    "小红书": {
        "tone": "真诚分享/场景种草/闺蜜口吻",
        "hook_time": "标题即钩子",
        "body_length": "300-800字",
        "tags": "10-20个",
        "title_style": "痛点+解决方案/数字+结果",
    },
    "百度": {
        "tone": "理性/数据支撑/低信任成本",
        "hook_style": "标题即精华",
        "title_max": 50,
        "body_length": "200-500字",
    },
    "快手": {
        "tone": "接地气/老铁文化/真实感",
        "opening": "老铁们/家人们",
        "title_style": "直接/利益点清晰",
    },
}


# ============================================================
# 文案生成引擎
# ============================================================

class CopyGenerator:
    """文案生成引擎"""

    def __init__(self):
        self.compliance_rules = self._load_compliance_rules()

    def _load_compliance_rules(self) -> List[str]:
        return [
            "最", "第一", "唯一", "100%", "百分之百",
            "治愈", "根治", "见效", "祖传秘方",
            "保本", "稳赚", "零风险", "保收益",
            "包过", "押题", "保录取",
            "加微信", "扫码",
        ]

    def check_compliance(self, text: str) -> Dict:
        """前置合规检查"""
        violations = []
        for rule in self.compliance_rules:
            if rule in text:
                violations.append({
                    "keyword": rule,
                    "position": text.index(rule),
                })
        return {
            "status": "FAIL" if violations else "PASS",
            "violations": violations,
            "violation_count": len(violations),
        }

    def generate_hook(self, style: str, params: Dict) -> str:
        """生成开头钩子"""
        templates = HOOK_TEMPLATES.get(style, HOOK_TEMPLATES["result_first"])
        import random
        template = random.choice(templates)
        try:
            return template.format(**params)
        except KeyError:
            return template

    def generate_variants(self, product: ProductInfo, platform: str,
                          count: int = 3) -> List[CopyVariant]:
        """生成多版本文案"""
        angles = [
            {"name": "功能卖点", "desc": "它能做什么"},
            {"name": "结果卖点", "desc": "用了会怎样"},
            {"name": "情感卖点", "desc": "它让你感觉如何"},
            {"name": "对比卖点", "desc": "比别人好在哪"},
            {"name": "稀缺卖点", "desc": "为什么只有它有"},
            {"name": "信任卖点", "desc": "为什么可信"},
        ]
        tones = ["理性/专业", "感性/共情", "紧迫/行动"]
        structures = list(STRUCTURE_TEMPLATES.keys())

        variants = []
        for i in range(min(count, len(angles))):
            angle = angles[i % len(angles)]
            tone = tones[i % len(tones)]
            structure = structures[i % len(structures)]

            hook_text = f"为{product.target_audience}解决{product.pain_points[0] if product.pain_points else '核心痛点'}"
            body_text = (
                f"很多{product.target_audience}都面临{product.pain_points[0] if product.pain_points else '这个困扰'}。"
                f"{product.name}的{product.selling_points[0] if product.selling_points else '核心卖点'}，"
                f"让{product.differentiators[0] if product.differentiators else '与众不同'}。"
            )

            variant = CopyVariant(
                version=f"{chr(65+i)}",
                angle=angle["name"],
                structure=STRUCTURE_TEMPLATES[structure]["name"],
                tone=tone,
                hook=f"【{angle['name']}】{hook_text}",
                body=body_text,
                cta=self._generate_cta(i),
            )
            variants.append(variant)

        return variants

    def _generate_cta(self, style_idx: int) -> str:
        ctas = [
            "想了解更多？评论区扣1，我私信你",
            "关注我，每天分享XX干货",
            "私信领取完整方案，仅限前50名",
            "你觉得呢？评论区聊聊你的看法",
            "建议收藏，以后用得上",
        ]
        return ctas[style_idx % len(ctas)]

    def generate_storyboard(self, product: ProductInfo, variants: List[CopyVariant],
                            platform: str) -> List[ShortVideoScript]:
        """生成分镜头脚本"""
        scenes = []
        time_codes = [(0, 3), (3, 8), (8, 15), (15, 25), (25, 30)]

        descriptions = [
            (f"特写: {product.pain_points[0] if product.pain_points else '用户痛点'}表情",
             "痛点BGM", "痛点关键词"),
            (f"中景: 展示{product.name}使用场景",
             "轻柔BGM渐入", "产品名+场景"),
            (f"近景: {product.name}产品特写 + 功能演示",
             "BGM渐强", "核心卖点"),
            (f"全景: 使用{product.name}后的效果展示",
             "轻快BGM", "效果文案"),
            (f"人物出镜: 面对镜头CTA",
             "CTA音效", "关注/私信引导"),
        ]

        for i, ((start, end), (visual, sound, subtitle)) in enumerate(zip(time_codes, descriptions)):
            voiceover = ""
            if i < len(variants):
                voiceover = variants[0].body[:50] if i == 2 else ""
            elif i == len(time_codes) - 1:
                voiceover = variants[0].cta if variants else "关注获取更多"

            scenes.append(ShortVideoScript(
                scene_id=f"{i+1:02d}",
                duration=f"{start}-{end}s",
                visual=visual,
                voiceover=voiceover or f"{product.name}介绍片段{i+1}",
                sound_effect=sound,
                subtitle=subtitle,
            ))

        return scenes

    def generate_script(self, product: ProductInfo, platform: str,
                        hook_style: str = "pain_point",
                        variant_count: int = 3) -> ScriptOutput:
        """生成完整短视频脚本"""
        variants = self.generate_variants(product, platform, variant_count)
        storyboard = self.generate_storyboard(product, variants, platform)

        # 合成完整文案做合规检查
        full_text = " ".join([v.body for v in variants])
        compliance = self.check_compliance(full_text)

        # 生成标题
        hook_params = {
            "skill": product.selling_points[0] if product.selling_points else "",
            "from_state": "不会",
            "to_state": "精通",
            "age": "30",
            "dream": "目标",
            "problem": product.pain_points[0] if product.pain_points else "问题",
            "pain_point": product.target_audience,
            "industry": product.category,
            "truth": "行业",
            "behavior": "做法",
        }
        title = self.generate_hook(hook_style, hook_params)

        return ScriptOutput(
            title=title,
            product=product.name,
            platform=platform,
            hook=title,
            variants=variants,
            storyboard=storyboard,
            compliance_check=compliance,
        )


# ============================================================
# 报告生成
# ============================================================

class ReportGenerator:
    """素材产出报告生成"""

    @staticmethod
    def format_script_output(output: ScriptOutput) -> str:
        lines = []
        sep = "━" * 55
        lines.append(sep)
        lines.append(" Ravy 素材产出报告")
        lines.append(sep)
        lines.append(f" 产品: {output.product}")
        lines.append(f" 平台: {output.platform}")
        lines.append(f" 标题: {output.title}")
        lines.append(f" 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        # 多版本
        lines.append(" ┌─ 多版本文案 ──────────────────────────────")
        for v in output.variants:
            lines.append(f" │")
            lines.append(f" │  版本{v.version} — {v.angle}")
            lines.append(f" │  结构: {v.structure}  调性: {v.tone}")
            lines.append(f" │  钩子: {v.hook}")
            lines.append(f" │  正文: {v.body[:80]}...")
            lines.append(f" │  CTA:  {v.cta}")
        lines.append(" └─────────────────────────────────────────────")
        lines.append("")

        # 分镜头
        if output.storyboard:
            lines.append(" ┌─ 分镜头脚本 ──────────────────────────────")
            for s in output.storyboard:
                lines.append(f" │  {s.scene_id} | {s.duration} | {s.visual}")
                lines.append(f" │    口播: {s.voiceover[:60]}")
            lines.append(" └─────────────────────────────────────────────")
            lines.append("")

        # 合规
        ck = output.compliance_check
        status_icon = "✅" if ck.get("status") == "PASS" else "❌"
        lines.append(f" ┌─ 合规自检 [{status_icon} {ck.get('status')}] ─────")
        if ck.get("violations"):
            for v in ck["violations"]:
                lines.append(f" │  ⚠ 违禁词 '{v['keyword']}' 位置 {v['position']}")
        lines.append(" └─────────────────────────────────────────────")

        lines.append("")
        lines.append(sep)
        return "\n".join(lines)


# ============================================================
# 主程序
# ============================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Ravy 爆款素材创作工具")
    parser.add_argument("--mode", "-m", choices=["script", "headline", "landing", "dm", "batch"],
                       default="script", help="生成模式")
    parser.add_argument("--product", "-p", default="", help="产品名称")
    parser.add_argument("--platform", "-f", choices=["抖音", "小红书", "百度", "快手"],
                       default="抖音", help="投放平台")
    parser.add_argument("--hook", "-k", choices=list(HOOK_TEMPLATES.keys()),
                       default="pain_point", help="开头钩子类型")
    parser.add_argument("--count", "-c", type=int, default=3, help="版本数量")
    parser.add_argument("--output", "-o", default="", help="输出文件路径")
    args = parser.parse_args()

    # 默认产品信息（交互式输入）
    if not args.product:
        product = ProductInfo(
            name="示例产品",
            category="教育",
            price="¥199",
            selling_points=["高效学习", "名师指导", "个性化方案"],
            differentiators=["AI智能匹配", "7x24答疑", "效果可量化"],
            target_audience="职场人",
            pain_points=["学习效率低", "没时间", "坚持不下去"],
        )
    else:
        product = ProductInfo(
            name=args.product,
            category="通用",
            price="",
            selling_points=[args.product],
            differentiators=["优质产品"],
            target_audience="目标用户",
            pain_points=["核心痛点"],
        )

    generator = CopyGenerator()

    if args.mode == "script":
        output = generator.generate_script(
            product, args.platform, args.hook, args.count
        )
        report = ReportGenerator.format_script_output(output)
        print(report)

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"\n[Ravy] 已保存: {args.output}")

    elif args.mode == "headline":
        variants = generator.generate_variants(product, args.platform, args.count)
        for v in variants:
            print(f"\n  [{v.version}] {v.angle} | {v.tone}")
            print(f"  标题: {v.hook[:50]}")

    elif args.mode == "batch":
        platforms = ["抖音", "小红书", "百度", "快手"]
        batch = BatchOutput(
            batch_id=f"BATCH-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            product=product.name,
            platform="多平台",
        )
        for p in platforms:
            output = generator.generate_script(
                product, p, args.hook, args.count
            )
            batch.outputs.append(output)
            print(f"\n[Ravy] {p} ✅")

        print(f"\n[Ravy] 批量完成: {len(batch.outputs)} 平台 × {args.count} 版本")
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(json.dumps(asdict(batch), ensure_ascii=False, indent=2))

    elif args.mode == "interactive":
        print("=" * 55)
        print(" Ravy 爆款素材创作工具")
        print(" 命令: script / headline / batch / exit")
        print("=" * 55)
        while True:
            cmd = input("\n Ravy> ").strip().lower()
            if cmd in ("exit", "q"):
                break
            elif cmd == "script":
                output = generator.generate_script(product, args.platform)
                print(ReportGenerator.format_script_output(output))
            else:
                print(" 未知命令")


if __name__ == "__main__":
    main()
