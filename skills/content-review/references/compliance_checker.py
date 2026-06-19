"""
Davy 合规检测工具
==================
全域广告违禁词检索 · 内容合规检测 · 违规位置标注 · 风险等级判定 · 同义改写 · 台账生成

依赖安装:
  pip install pyyaml re json datetime pathlib

可选依赖（增强功能）:
  pip install paddlepaddle paddleocr     # OCR 图片文字检测
  pip install easyocr                     # 轻量 OCR
  pip install transformers torch          # NLP 文本分类
  pip install opencv-python               # 图片处理
  pip install moviepy                     # 视频处理

运行方式:
  python compliance_checker.py --input <file_or_dir> --output <report_dir>

参考开源项目:
  - 敏感词库: https://github.com/houbb/sensitive-word
  - 文本风控: https://github.com/PaddlePaddle/PaddleNLP
  - OCR: https://github.com/PaddlePaddle/PaddleOCR
  - NLP: https://huggingface.co/shibing624/text2vec-base-chinese
"""

import json
import re
import yaml
import os
import sys
from datetime import datetime

# Windows GBK 兼容
if sys.platform == "win32":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", errors="replace", closefd=False)
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = open(sys.stderr.fileno(), "w", encoding="utf-8", errors="replace", closefd=False)
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict

# ============================================================
# 数据类型定义
# ============================================================

@dataclass
class ViolationItem:
    violation_id: str
    type: str
    keyword: str
    matched_text: str
    level: str  # critical / high / medium / low
    platform_penalty: str
    rewrite_suggestion: str
    position: Dict = field(default_factory=dict)
    law_ref: str = ""

@dataclass
class ReviewReport:
    material_id: str
    material_type: str  # script / cover / landing / dm
    platform: str
    industry: str
    review_time: str
    conclusion: str  # pass / conditional / reject
    violations: List[ViolationItem] = field(default_factory=list)
    risk_score: int = 100
    summary: str = ""

@dataclass
class BatchReport:
    batch_id: str
    total_count: int
    pass_count: int
    conditional_count: int
    reject_count: int
    reports: List[ReviewReport] = field(default_factory=list)
    statistics: Dict = field(default_factory=dict)


# ============================================================
# 违禁词加载
# ============================================================

class SensitiveWordLoader:
    """加载敏感词配置"""

    def __init__(self, config_path: str = None):
        self.config_path = config_path or os.path.join(
            os.path.dirname(__file__), "sensitive_words.yaml"
        )
        self.keywords: List[Dict] = []
        self.load()

    def load(self):
        with open(self.config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        for category_key in ["extreme_words", "medical_words", "financial_words",
                             "education_words", "platform_violation_words", "sensitive_words"]:
            category = config.get(category_key, [])
            for group in category:
                for entry in group.get("words", []):
                    self.keywords.append(entry)

        print(f"[Davy] 已加载 {len(self.keywords)} 条敏感词规则")

    def get_keywords_by_level(self, level: str) -> List[Dict]:
        return [kw for kw in self.keywords if kw.get("level") == level]

    def get_keywords_by_type(self, keyword_type: str) -> List[Dict]:
        return [kw for kw in self.keywords if kw.get("type") == keyword_type]


# ============================================================
# 核心检测引擎
# ============================================================

class ComplianceChecker:
    """合规检测引擎"""

    def __init__(self, word_loader: SensitiveWordLoader):
        self.word_loader = word_loader
        self.law_map = {
            "绝对化用语": "《广告法》第9条",
            "医疗功效": "《广告法》第17条",
            "医疗资质暗示": "《广告法》第16条",
            "金融保本": "《广告法》第25条",
            "金融诱导": "《广告法》第25条",
            "教育承诺": "《广告法》第24条",
            "平台引流": "《互联网广告管理办法》第9条",
            "诱导点击": "《互联网广告管理办法》第9条",
            "虚假承诺": "《广告法》第28条",
        }
        self.platform_penalty_map = {
            "critical": "封号",
            "high": "限流封禁",
            "medium": "警告",
            "low": "限流观察",
        }

    def scan_text(self, text: str, material_id: str = "",
                  platform: str = "通用", industry: str = "通用") -> List[ViolationItem]:
        """对文本执行违禁词扫描，返回违规项列表"""
        violations = []
        for kw in self.word_loader.keywords:
            word = kw.get("word", "")
            if not word:
                continue

            # 检查平台适用性
            platforms = kw.get("platforms", None)
            if platforms and platform not in platforms and "all" not in platforms:
                continue

            # 正则匹配（支持中文全词匹配）
            pattern = re.escape(word)
            for match in re.finditer(pattern, text):
                start = match.start()
                end = match.end()
                matched = text[start:end]

                # 获取上下文
                ctx_start = max(0, start - 20)
                ctx_end = min(len(text), end + 20)
                context = text[ctx_start:ctx_end]

                # 确定行号
                line_num = text[:start].count("\n") + 1

                # 获取改写建议
                rewrites = kw.get("rewrite", [])
                rewrite_suggestion = rewrites[0] if rewrites else ""

                # 获取涉及法规
                kw_type = kw.get("type", "")
                law_ref = self.law_map.get(kw_type, "")

                # 获取风险等级
                level = kw.get("level", "medium")

                violation = ViolationItem(
                    violation_id=f"VIO-{material_id}-{len(violations) + 1:04d}",
                    type=kw_type,
                    keyword=word,
                    matched_text=matched,
                    level=level,
                    platform_penalty=self.platform_penalty_map.get(level, "警告"),
                    rewrite_suggestion=rewrite_suggestion,
                    position={
                        "全文偏移": {"start": start, "end": end},
                        "所在行": line_num,
                        "所在句": context,
                        "前后文": context,
                    },
                    law_ref=law_ref,
                )
                violations.append(violation)

        # 去重（同一个词在同一位置重复匹配）
        seen = set()
        unique_violations = []
        for v in violations:
            key = (v.keyword, v.position.get("全文偏移", {}).get("start", 0))
            if key not in seen:
                seen.add(key)
                unique_violations.append(v)

        return unique_violations

    def calculate_risk_score(self, violations: List[ViolationItem]) -> int:
        """根据违规项计算风险评分"""
        score = 100
        level_deduction = {
            "critical": 30,
            "high": 20,
            "medium": 10,
            "low": 5,
        }
        for v in violations:
            score -= level_deduction.get(v.level, 5)
        return max(0, score)

    def determine_conclusion(self, violations: List[ViolationItem]) -> str:
        """根据违规项确定审核结论"""
        levels = [v.level for v in violations]
        if "critical" in levels:
            return "reject"
        elif levels.count("high") >= 2 or "high" in levels:
            return "conditional"
        elif levels.count("medium") >= 3:
            return "conditional"
        elif violations:
            return "conditional"
        return "pass"

    def review_material(self, text: str, material_id: str,
                        material_type: str = "script",
                        platform: str = "通用",
                        industry: str = "通用") -> ReviewReport:
        """审核单条素材"""
        violations = self.scan_text(text, material_id, platform, industry)
        risk_score = self.calculate_risk_score(violations)
        conclusion = self.determine_conclusion(violations)

        level_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "pass": 0}
        for v in violations:
            level_counts[v.level] = level_counts.get(v.level, 0) + 1
        if not violations:
            level_counts["pass"] = 1

        summary = (
            f"审核{len(violations)}处违规 | "
            f"严重{level_counts['critical']} 高危{level_counts['high']} "
            f"中度{level_counts['medium']} 轻度{level_counts['low']}"
        )

        return ReviewReport(
            material_id=material_id,
            material_type=material_type,
            platform=platform,
            industry=industry,
            review_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            conclusion=conclusion,
            violations=violations,
            risk_score=risk_score,
            summary=summary,
        )


# ============================================================
# 违规改写引擎
# ============================================================

class ViolationRewriter:
    """违规内容改写引擎"""

    def __init__(self, word_loader: SensitiveWordLoader):
        self.word_loader = word_loader
        self.rewrite_rules = self._build_rewrite_rules()

    def _build_rewrite_rules(self) -> Dict[str, List[str]]:
        rules = {}
        for kw in self.word_loader.keywords:
            word = kw.get("word", "")
            rewrites = kw.get("rewrite", [])
            if word and rewrites:
                rules[word] = rewrites
        return rules

    def rewrite_text(self, text: str, violations: List[ViolationItem],
                     strategy: str = "smart_auto") -> Tuple[str, List[Dict]]:
        """对违规文本执行改写，返回改写后文本和修改记录"""
        modified = text
        changes = []

        if strategy == "minimal_change":
            # 按违规位置从后往前替换（避免偏移变化）
            violations_sorted = sorted(
                violations,
                key=lambda v: v.position.get("全文偏移", {}).get("start", 0),
                reverse=True,
            )
            for v in violations_sorted:
                start = v.position.get("全文偏移", {}).get("start", 0)
                end = v.position.get("全文偏移", {}).get("end", 0)
                if start >= 0 and end > start and v.rewrite_suggestion:
                    old = modified[start:end]
                    new = v.rewrite_suggestion
                    modified = modified[:start] + new + modified[end:]
                    changes.append({
                        "keyword": v.keyword,
                        "old": old,
                        "new": new,
                        "position": {"start": start, "end": end},
                    })
        else:
            # 智能改写：使用预定义改写规则
            for v in violations:
                if v.rewrite_suggestion and v.keyword in modified:
                    modified = modified.replace(v.keyword, v.rewrite_suggestion, 1)
                    changes.append({
                        "keyword": v.keyword,
                        "old": v.keyword,
                        "new": v.rewrite_suggestion,
                    })

        return modified, changes

    def auto_verify(self, original: str, rewritten: str,
                    checker: ComplianceChecker) -> Dict:
        """自动验证改写结果"""
        original_violations = checker.scan_text(original)
        rewritten_violations = checker.scan_text(rewritten)

        original_count = len(original_violations)
        rewritten_count = len(rewritten_violations)
        cleared = original_count - rewritten_count

        return {
            "original_violations": original_count,
            "rewritten_violations": rewritten_count,
            "cleared": max(0, cleared),
            "new_violations_introduced": rewritten_count,
            "all_cleared": rewritten_count == 0,
            "status": "pass" if rewritten_count == 0 else "partial",
        }


# ============================================================
# 报告生成
# ============================================================

class ReportGenerator:
    """审核报告生成器"""

    @staticmethod
    def generate_text_report(report: ReviewReport) -> str:
        """生成文本格式审核报告"""
        return ReportGenerator._build_report_lines(report)

    @staticmethod
    def _build_report_lines(report: ReviewReport) -> str:
        """构建报告文本"""
        lines = []
        lines.append("━" * 55)
        lines.append(" Davy 内容合规审核报告")
        lines.append("━" * 55)
        lines.append(f" 素材ID:     {report.material_id}")
        lines.append(f" 素材类型:   {report.material_type}")
        lines.append(f" 投放平台:   {report.platform}")
        lines.append(f" 行业分类:   {report.industry}")
        lines.append(f" 审核时间:   {report.review_time}")
        lines.append(f" 风控评分:   {report.risk_score}")
        lines.append(f" 审核结论:   {report.conclusion.upper()}")
        lines.append(f" 违规摘要:   {report.summary}")
        lines.append("━" * 55)

        if report.violations:
            lines.append("")
            lines.append(" ┌─ 违规项列表 ──────────────────────────────")
            for i, v in enumerate(report.violations, 1):
                lines.append(f" │")
                lines.append(f" │  违规{i}: \"{v.matched_text}\"")
                lines.append(f" │  类型: {v.type}  等级: {v.level.upper()}")
                lines.append(f" │  判定: {v.platform_penalty}")
                lines.append(f" │  位置: 第{v.position.get('所在行', '?')}行 "
                             f"(偏移{v.position.get('全文偏移', {}).get('start', 0)}-"
                             f"{v.position.get('全文偏移', {}).get('end', 0)})")
                lines.append(f" │  法规: {v.law_ref}")
                if v.rewrite_suggestion:
                    lines.append(f" │  建议: → \"{v.rewrite_suggestion}\"")
                lines.append(f" │")
            lines.append(" └─────────────────────────────────────────────")

        lines.append("")
        lines.append("━" * 55)
        return "\n".join(lines)

    @staticmethod
    def generate_json_report(report: ReviewReport) -> str:
        """生成 JSON 格式审核报告"""
        return json.dumps(asdict(report), ensure_ascii=False, indent=2)

    @staticmethod
    def generate_batch_report(batch: BatchReport) -> str:
        """生成批量审核报告"""
        lines = []
        lines.append("━" * 55)
        lines.append(" Davy 批量合规审核报告")
        lines.append("━" * 55)
        lines.append(f" 批次:      {batch.batch_id}")
        lines.append(f" 总量:      {batch.total_count} 条")
        lines.append(f" 审核时间:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("━" * 55)

        pass_rate = (batch.pass_count / batch.total_count * 100) if batch.total_count else 0
        cond_rate = (batch.conditional_count / batch.total_count * 100) if batch.total_count else 0
        reject_rate = (batch.reject_count / batch.total_count * 100) if batch.total_count else 0

        lines.append(f" 合规通过: {batch.pass_count}条 ({pass_rate:.1f}%)")
        lines.append(f" 条件通过: {batch.conditional_count}条 ({cond_rate:.1f}%)")
        lines.append(f" 拒绝投放: {batch.reject_count}条 ({reject_rate:.1f}%)")

        if batch.statistics:
            lines.append("")
            lines.append(" 违规类型分布:")
            for vt, count in sorted(
                batch.statistics.get("violation_types", {}).items(),
                key=lambda x: x[1], reverse=True
            ):
                pct = count / max(1, sum(batch.statistics.get("violation_types", {}).values())) * 100
                lines.append(f"   {vt}: {count}次 ({pct:.1f}%)")

        lines.append("")
        lines.append("━" * 55)
        return "\n".join(lines)


# ============================================================
# 台账生成
# ============================================================

class LedgerGenerator:
    """风控台账生成器"""

    @staticmethod
    def generate_daily_ledger(reports: List[ReviewReport], date: str = "") -> str:
        """生成每日审核台账"""
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        total = len(reports)
        pass_count = sum(1 for r in reports if r.conclusion == "pass")
        conditional_count = sum(1 for r in reports if r.conclusion == "conditional")
        reject_count = sum(1 for r in reports if r.conclusion == "reject")

        # 统计违规类型
        violation_types = {}
        industry_stats = {}
        for r in reports:
            industry_stats[r.industry] = industry_stats.get(r.industry, 0) + 1
            for v in r.violations:
                violation_types[v.type] = violation_types.get(v.type, 0) + 1

        lines = []
        lines.append("━" * 60)
        lines.append(" Davy 合规审核台账")
        lines.append(f" 日期: {date}")
        lines.append(f" 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("━" * 60)
        lines.append(f" 审核总量:      {total} 条素材")
        lines.append(f" 合规通过:      {pass_count}条 ({pass_count/max(1,total)*100:.1f}%)")
        lines.append(f" 条件通过:      {conditional_count}条 ({conditional_count/max(1,total)*100:.1f}%)")
        lines.append(f" 拒绝投放:      {reject_count}条 ({reject_count/max(1,total)*100:.1f}%)")
        lines.append(f" 拒绝率:        {reject_count/max(1,total)*100:.1f}%")
        lines.append("")
        lines.append(" 违规类型分布:")
        for vt, count in sorted(violation_types.items(), key=lambda x: x[1], reverse=True):
            pct = count / max(1, total) * 100
            lines.append(f"   {vt}: {count}次 ({pct:.1f}%)")

        lines.append("")
        lines.append(" 按行业分布:")
        for ind, count in sorted(industry_stats.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"   {ind}: {count}条 ({count/max(1,total)*100:.1f}%)")

        lines.append("")
        lines.append("━" * 60)
        return "\n".join(lines)


# ============================================================
# 主程序
# ============================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Davy 合规检测工具")
    parser.add_argument("--input", "-i", help="输入文件或目录路径")
    parser.add_argument("--output", "-o", default="./reports", help="输出报告目录")
    parser.add_argument("--config", "-c", default="", help="敏感词配置文件路径")
    parser.add_argument("--mode", "-m", choices=["single", "batch", "rewrite", "ledger"],
                       default="single", help="运行模式")
    parser.add_argument("--platform", default="通用", help="投放平台")
    parser.add_argument("--industry", default="通用", help="行业分类")
    parser.add_argument("--format", "-f", choices=["text", "json"], default="text",
                       help="输出格式")
    args = parser.parse_args()

    # 初始化
    loader = SensitiveWordLoader(args.config)
    checker = ComplianceChecker(loader)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "rewrite":
        rewriter = ViolationRewriter(loader)
        print("=" * 60)
        print(" Davy 违规改写引擎 就绪")
        print(" 改写规则数:", len(rewriter.rewrite_rules))
        print("=" * 60)
        return

    if args.input:
        path = Path(args.input)
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            report = checker.review_material(
                text, material_id=path.stem,
                platform=args.platform, industry=args.industry,
            )
            if args.format == "json":
                output = ReportGenerator.generate_json_report(report)
            else:
                output = ReportGenerator.generate_text_report(report)

            print(output)
            report_path = output_dir / f"{path.stem}_report.{args.format}"
            report_path.write_text(output, encoding="utf-8")
            print(f"\n[Davy] 报告已保存: {report_path}")

        elif path.is_dir():
            reports = []
            for file_path in path.glob("*.txt"):
                text = file_path.read_text(encoding="utf-8")
                report = checker.review_material(
                    text, material_id=file_path.stem,
                    platform=args.platform, industry=args.industry,
                )
                reports.append(report)
                if args.format == "json":
                    output = ReportGenerator.generate_json_report(report)
                else:
                    output = ReportGenerator.generate_text_report(report)
                report_path = output_dir / f"{file_path.stem}_report.{args.format}"
                report_path.write_text(output, encoding="utf-8")
                print(f"[Davy] {file_path.name} → {report_path}")

            batch = BatchReport(
                batch_id=f"BATCH-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                total_count=len(reports),
                pass_count=sum(1 for r in reports if r.conclusion == "pass"),
                conditional_count=sum(1 for r in reports if r.conclusion == "conditional"),
                reject_count=sum(1 for r in reports if r.conclusion == "reject"),
                reports=reports,
            )

            batch_report = ReportGenerator.generate_batch_report(batch)
            batch_path = output_dir / "batch_report.txt"
            batch_path.write_text(batch_report, encoding="utf-8")
            print(f"\n[Davy] 批量报告已保存: {batch_path}")

            # 同时生成台账
            ledger = LedgerGenerator.generate_daily_ledger(reports)
            ledger_path = output_dir / "daily_ledger.txt"
            ledger_path.write_text(ledger, encoding="utf-8")
            print(f"[Davy] 台账已保存: {ledger_path}")

    else:
        # 交互模式
        print("=" * 60)
        print(" Davy 合规检测工具 v3.2.1")
        print(" 敏感词规则数:", len(loader.keywords))
        print("=" * 60)
        print(" 输入内容进行检测（输入 'exit' 退出）")
        print("-" * 60)

        while True:
            text = input("\n 待检测文本: ").strip()
            if text.lower() in ("exit", "quit", "q"):
                break
            if not text:
                continue

            report = checker.review_material(text, material_id="interactive")
            print()
            print(ReportGenerator.generate_text_report(report))


if __name__ == "__main__":
    main()
