"""
Kevy 投流哨兵 - 全域投放优化工具
===================================
多广告账户数据监控 · KPI 异常告警 · 自动优化 · 消息推送 · 日志汇总

依赖安装:
  pip install apscheduler requests pyyaml

可选依赖:
  pip install celery redis                     # 分布式任务
  pip install prometheus-client                # 指标导出
  pip install oceanengine-python-sdk           # 巨量引擎 SDK
  pip install baiduads_sdk                     # 百度营销 SDK

运行方式:
  python traffic_sentinel.py --mode monitor    # 启动监控
  python traffic_sentinel.py --mode report     # 生成日报
  python traffic_sentinel.py --mode check      # 单次检查
"""

import json
import hashlib
import os
import sys
import time
import yaml
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Callable
from enum import Enum

# Windows GBK 兼容：替换 stdout/stderr 编码
if sys.platform == "win32":
    _orig_stdout = sys.stdout
    _orig_stderr = sys.stderr
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", errors="replace", closefd=False)
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = open(sys.stderr.fileno(), "w", encoding="utf-8", errors="replace", closefd=False)

# ============================================================
# 类型定义
# ============================================================

class Platform(Enum):
    OCEAN_ENGINE = "巨量引擎"
    QIAN_CHUAN = "千川"
    BAIDU = "百度营销"
    KUAISHOU = "磁力引擎"
    TENCENT = "朋友圈(腾讯)"

class AlertLevel(Enum):
    P0_EMERGENCY = "P0_EMERGENCY"
    P1_HIGH = "P1_HIGH"
    P2_WARNING = "P2_WARNING"
    P3_INFO = "P3_INFO"

class CampaignStatus(Enum):
    DELIVERING = "投放中"
    PAUSED = "已暂停"
    AUDITING = "审核中"
    REJECTED = "审核拒绝"
    FINISHED = "已结束"
    FROZEN = "已封禁"

@dataclass
class AccountConfig:
    platform: str
    account_id: str
    account_name: str
    industry: str
    daily_budget: float
    monthly_budget: float
    target_cpl: float
    target_roi: float
    is_active: bool = True
    alert_webhook: str = ""

@dataclass
class CampaignMetrics:
    campaign_id: str
    campaign_name: str
    platform: str
    account_id: str
    status: str
    cost: float = 0.0
    impressions: int = 0
    clicks: int = 0
    conversions: int = 0
    ctr: float = 0.0
    cpc: float = 0.0
    cpm: float = 0.0
    cpl: float = 0.0
    cvr: float = 0.0
    gmv: float = 0.0
    roi: float = 0.0
    bid: float = 0.0
    budget: float = 0.0
    created_time: str = ""
    fetched_time: str = ""

@dataclass
class AlertEvent:
    alert_id: str
    level: AlertLevel
    platform: str
    account_id: str
    account_name: str
    alert_type: str
    alert_message: str
    metrics_snapshot: Dict
    triggered_at: str
    resolved: bool = False
    resolved_at: str = ""
    action_taken: str = ""

@dataclass
class OptimizationAction:
    action_id: str
    action_type: str  # stop / copy / bid / schedule
    platform: str
    account_id: str
    campaign_id: str
    reason: str
    detail: str
    executed_at: str
    status: str = "executed"

@dataclass
class DailyReport:
    report_date: str
    total_cost: float = 0.0
    total_impressions: int = 0
    total_clicks: int = 0
    total_conversions: int = 0
    total_gmv: float = 0.0
    avg_ctr: float = 0.0
    avg_cpc: float = 0.0
    avg_cpl: float = 0.0
    avg_roi: float = 0.0
    platform_breakdown: Dict = field(default_factory=dict)
    alerts_today: List[AlertEvent] = field(default_factory=list)
    actions_today: List[OptimizationAction] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)


# ============================================================
# 平台数据适配器（模拟/占位 - 需对接真实 OpenAPI）
# ============================================================

class PlatformAdapter:
    """平台数据适配器基类"""

    def __init__(self, platform: str, credentials: Dict):
        self.platform = platform
        self.credentials = credentials
        self.token = None

    def refresh_token(self) -> bool:
        """刷新 access_token"""
        raise NotImplementedError

    def fetch_accounts(self) -> List[str]:
        """获取广告账户列表"""
        raise NotImplementedError

    def fetch_campaigns(self, account_id: str) -> List[CampaignMetrics]:
        """拉取计划级数据"""
        raise NotImplementedError

    def update_bid(self, account_id: str, campaign_id: str,
                   new_bid: float) -> bool:
        """修改出价"""
        raise NotImplementedError

    def update_status(self, account_id: str, campaign_id: str,
                      status: str) -> bool:
        """启停计划"""
        raise NotImplementedError


class MockPlatformAdapter(PlatformAdapter):
    """模拟适配器（用于开发和测试）"""

    def __init__(self, platform: str, credentials: Dict):
        super().__init__(platform, credentials)
        self.token = "mock_token_" + platform

    def refresh_token(self) -> bool:
        self.token = "mock_token_" + self.platform + "_" + str(int(time.time()))
        return True

    def fetch_accounts(self) -> List[str]:
        return [self.credentials.get("account_id", "mock_account")]

    def fetch_campaigns(self, account_id: str) -> List[CampaignMetrics]:
        now = datetime.now()
        import random
        campaigns = []
        for i in range(random.randint(3, 8)):
            cost = random.uniform(100, 5000)
            impressions = random.randint(5000, 100000)
            clicks = int(impressions * random.uniform(0.005, 0.05))
            conversions = int(clicks * random.uniform(0.01, 0.15))
            gmv = conversions * random.uniform(50, 500)
            ctr = (clicks / impressions * 100) if impressions > 0 else 0
            cpc = (cost / clicks) if clicks > 0 else 0
            cpl = (cost / conversions) if conversions > 0 else 0
            cvr = (conversions / clicks * 100) if clicks > 0 else 0
            roi = (gmv / cost) if cost > 0 else 0

            campaigns.append(CampaignMetrics(
                campaign_id=f"CAMP-{self.platform[:2]}-{i:04d}",
                campaign_name=f"测试计划-{i}",
                platform=self.platform,
                account_id=account_id,
                status=random.choice([s.value for s in CampaignStatus]),
                cost=round(cost, 2),
                impressions=impressions,
                clicks=clicks,
                conversions=conversions,
                ctr=round(ctr, 2),
                cpc=round(cpc, 2),
                cpm=round(cost / impressions * 1000, 2) if impressions > 0 else 0,
                cpl=round(cpl, 2),
                cvr=round(cvr, 2),
                gmv=round(gmv, 2),
                roi=round(roi, 2),
                bid=round(random.uniform(5, 50), 2),
                budget=round(random.uniform(500, 10000), 0),
                created_time=now.strftime("%Y-%m-%d %H:%M:%S"),
                fetched_time=now.strftime("%Y-%m-%d %H:%M:%S"),
            ))
        return campaigns

    def update_bid(self, account_id: str, campaign_id: str,
                   new_bid: float) -> bool:
        print(f"  [Kevy] {self.platform} {campaign_id}: 出价调整为 ¥{new_bid}")
        return True

    def update_status(self, account_id: str, campaign_id: str,
                      status: str) -> bool:
        action = "启动" if status == "投放中" else "暂停"
        print(f"  [Kevy] {self.platform} {campaign_id}: {action}")
        return True


# ============================================================
# KPI 计算引擎
# ============================================================

class KPICalculator:
    """KPI 指标计算与异常检测"""

    @staticmethod
    def calculate(campaign: CampaignMetrics) -> CampaignMetrics:
        """补充计算所有派生指标"""
        if campaign.impressions > 0:
            campaign.ctr = round(campaign.clicks / campaign.impressions * 100, 2)
            campaign.cpm = round(campaign.cost / campaign.impressions * 1000, 2)
        if campaign.clicks > 0:
            campaign.cpc = round(campaign.cost / campaign.clicks, 2)
            campaign.cvr = round(campaign.conversions / campaign.clicks * 100, 2)
        if campaign.conversions > 0:
            campaign.cpl = round(campaign.cost / campaign.conversions, 2)
        if campaign.cost > 0:
            campaign.roi = round(campaign.gmv / campaign.cost, 2)
        return campaign


# ============================================================
# 异常检测引擎
# ============================================================

class AnomalyDetector:
    """KPI 异常检测"""

    def __init__(self, config: Dict = None):
        self.config = config or {
            "ctr_min": 0.3,
            "ctr_drop_ratio": 0.4,
            "cpc_spike_ratio": 0.5,
            "cpl_over_ratio": 0.2,
            "cvr_drop_ratio": 0.4,
            "roi_drop_ratio": 0.2,
            "budget_warn_ratio": 0.8,
            "cost_surge_ratio": 1.0,     # vs 前1小时均值
            "cost_plunge_ratio": 0.5,     # vs 前1小时均值
        }
        self.history: Dict[str, List[CampaignMetrics]] = {}

    def update_history(self, campaign: CampaignMetrics):
        """更新历史快照"""
        key = f"{campaign.platform}:{campaign.campaign_id}"
        if key not in self.history:
            self.history[key] = []
        self.history[key].append(campaign)
        if len(self.history[key]) > 24:  # 保留最近24个快照(2小时)
            self.history[key] = self.history[key][-24:]

    def _get_previous(self, campaign: CampaignMetrics) -> Optional[CampaignMetrics]:
        """获取上一个快照"""
        key = f"{campaign.platform}:{campaign.campaign_id}"
        history = self.history.get(key, [])
        return history[-1] if len(history) >= 1 else None

    def detect(self, campaign: CampaignMetrics,
               account: AccountConfig) -> List[AlertEvent]:
        """检测单条计划的异常"""
        alerts = []
        prev = self._get_previous(campaign)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if campaign.cost <= 0:
            return alerts

        # 1. CTR 过低
        if campaign.ctr < self.config["ctr_min"] and campaign.impressions > 1000:
            alerts.append(AlertEvent(
                alert_id=self._gen_id(),
                level=AlertLevel.P2_WARNING,
                platform=campaign.platform,
                account_id=account.account_id,
                account_name=account.account_name,
                alert_type="CTR过低",
                alert_message=(
                    f"计划 {campaign.campaign_name} CTR={campaign.ctr}% "
                    f"低于阈值 {self.config['ctr_min']}%"
                ),
                metrics_snapshot=asdict(campaign),
                triggered_at=now,
                action_taken="建议检查素材和定向"
            ))

        # 2. CPC 飙升
        if prev and prev.cpc > 0:
            cpc_change = (campaign.cpc - prev.cpc) / prev.cpc
            if cpc_change > self.config["cpc_spike_ratio"]:
                alerts.append(AlertEvent(
                    alert_id=self._gen_id(),
                    level=AlertLevel.P2_WARNING,
                    platform=campaign.platform,
                    account_id=account.account_id,
                    account_name=account.account_name,
                    alert_type="CPC飙升",
                    alert_message=(
                        f"计划 {campaign.campaign_name} CPC "
                        f"从 ¥{prev.cpc} 升至 ¥{campaign.cpc} "
                        f"(+{cpc_change*100:.0f}%)"
                    ),
                    metrics_snapshot=asdict(campaign),
                    triggered_at=now,
                    action_taken="建议降价5-10%或检查竞争环境"
                ))

        # 3. CPL 超标
        if account.target_cpl > 0 and campaign.cpl > 0:
            cpl_ratio = campaign.cpl / account.target_cpl
            if cpl_ratio > 1.5:
                alerts.append(AlertEvent(
                    alert_id=self._gen_id(),
                    level=AlertLevel.P1_HIGH,
                    platform=campaign.platform,
                    account_id=account.account_id,
                    account_name=account.account_name,
                    alert_type="CPL严重超标",
                    alert_message=(
                        f"计划 {campaign.campaign_name} "
                        f"CPL=¥{campaign.cpl} 目标CPL=¥{account.target_cpl} "
                        f"超出{cpl_ratio:.1f}倍"
                    ),
                    metrics_snapshot=asdict(campaign),
                    triggered_at=now,
                    action_taken="建议暂停计划或大幅降价"
                ))
            elif cpl_ratio > 1.2:
                alerts.append(AlertEvent(
                    alert_id=self._gen_id(),
                    level=AlertLevel.P2_WARNING,
                    platform=campaign.platform,
                    account_id=account.account_id,
                    account_name=account.account_name,
                    alert_type="CPL超标",
                    alert_message=(
                        f"计划 {campaign.campaign_name} "
                        f"CPL=¥{campaign.cpl} 超出目标{cpl_ratio:.0%}"
                    ),
                    metrics_snapshot=asdict(campaign),
                    triggered_at=now,
                    action_taken="建议降价5%观察"
                ))

        # 4. ROI 跌破
        if account.target_roi > 0 and campaign.roi > 0:
            roi_ratio = campaign.roi / account.target_roi
            if roi_ratio < 0.5:
                alerts.append(AlertEvent(
                    alert_id=self._gen_id(),
                    level=AlertLevel.P1_HIGH,
                    platform=campaign.platform,
                    account_id=account.account_id,
                    account_name=account.account_name,
                    alert_type="ROI跌破50%",
                    alert_message=(
                        f"计划 {campaign.campaign_name} "
                        f"ROI={campaign.roi} 目标ROI={account.target_roi} "
                        f"仅达目标的{roi_ratio:.0%}"
                    ),
                    metrics_snapshot=asdict(campaign),
                    triggered_at=now,
                    action_taken="已自动关停"
                ))
            elif roi_ratio < 0.8:
                alerts.append(AlertEvent(
                    alert_id=self._gen_id(),
                    level=AlertLevel.P2_WARNING,
                    platform=campaign.platform,
                    account_id=account.account_id,
                    account_name=account.account_name,
                    alert_type="ROI偏低",
                    alert_message=(
                        f"计划 {campaign.campaign_name} "
                        f"ROI={campaign.roi} 目标ROI={account.target_roi} "
                        f"达到{roi_ratio:.0%}"
                    ),
                    metrics_snapshot=asdict(campaign),
                    triggered_at=now,
                    action_taken="建议检查转化链路"
                ))

        # 5. 转化率为零但消耗高
        if campaign.conversions == 0 and campaign.cost > 500:
            alerts.append(AlertEvent(
                alert_id=self._gen_id(),
                level=AlertLevel.P2_WARNING,
                platform=campaign.platform,
                account_id=account.account_id,
                account_name=account.account_name,
                alert_type="空耗计划",
                alert_message=(
                    f"计划 {campaign.campaign_name} "
                    f"消耗¥{campaign.cost} 但转化为0"
                ),
                metrics_snapshot=asdict(campaign),
                triggered_at=now,
                action_taken="建议暂停检查转化链路"
            ))

        self.update_history(campaign)
        return alerts

    def _gen_id(self) -> str:
        return f"ALT-{int(time.time()*1000)}-{hashlib.md5(str(time.time()).encode()).hexdigest()[:6]}"


# ============================================================
# 自动优化引擎
# ============================================================

class AutoOptimizer:
    """自动化投放优化"""

    def __init__(self, platform_adapters: Dict[str, PlatformAdapter]):
        self.adapters = platform_adapters
        self.actions: List[OptimizationAction] = []
        self.daily_stop_count: Dict[str, int] = {}
        self.daily_copy_count: Dict[str, int] = {}
        self.bid_adjust_count: Dict[str, int] = {}
        self.config = {
            "new_campaign_protection_min": 120,
            "daily_stop_limit_ratio": 0.2,
            "stop_interval_sec": 120,
            "copy_max_per_campaign": 3,
            "daily_copy_limit": 10,
            "bid_adjust_min_interval_min": 120,
            "bid_daily_limit": 3,
            "bid_max_adjust_ratio": 0.15,
        }

    def check_and_stop_inefficient(self, campaigns: List[CampaignMetrics],
                                   account: AccountConfig) -> List[OptimizationAction]:
        """检查并关停低效计划"""
        actions = []
        today = datetime.now().strftime("%Y-%m-%d")
        daily_key = f"{account.platform}:{account.account_id}"

        stop_count = self.daily_stop_count.get(daily_key, 0)
        total_count = len(campaigns)
        stop_limit = max(1, int(total_count * self.config["daily_stop_limit_ratio"]))

        if stop_count >= stop_limit:
            return actions  # 已达日关停上限

        for camp in sorted(campaigns, key=lambda c: c.roi):
            if self.daily_stop_count.get(daily_key, 0) >= stop_limit:
                break

            # 保护新计划
            if self._is_new_campaign(camp):
                continue

            # 判定条件
            should_stop = False
            reason = ""

            if camp.cost > account.target_cpl * 2 and camp.conversions == 0:
                should_stop = True
                reason = f"0转化超花费 ¥{camp.cost}"
            elif camp.ctr < 0.1 and camp.impressions > 5000:
                should_stop = True
                reason = f"CTR={camp.ctr}% 过低"
            elif account.target_cpl > 0 and camp.cpl > account.target_cpl * 2:
                should_stop = True
                reason = f"CPL=¥{camp.cpl} 超目标2倍"
            elif camp.roi > 0 and account.target_roi > 0 and camp.roi < 0.3:
                should_stop = True
                reason = f"ROI={camp.roi} 过低"

            if should_stop:
                success = self._stop_campaign(account, camp.campaign_id)
                if success:
                    action = OptimizationAction(
                        action_id=f"STOP-{int(time.time())}-{camp.campaign_id[-4:]}",
                        action_type="stop",
                        platform=account.platform,
                        account_id=account.account_id,
                        campaign_id=camp.campaign_id,
                        reason=reason,
                        detail=f"自动关停: {reason}",
                        executed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    actions.append(action)
                    self.daily_stop_count[daily_key] = self.daily_stop_count.get(daily_key, 0) + 1
                    self.actions.append(action)

        return actions

    def check_and_copy_high_performing(
            self, campaigns: List[CampaignMetrics],
            account: AccountConfig) -> List[OptimizationAction]:
        """复制优质计划"""
        actions = []
        today_key = f"{account.platform}:{account.account_id}"
        copy_count = self.daily_copy_count.get(today_key, 0)

        if copy_count >= self.config["daily_copy_limit"]:
            return actions

        for camp in sorted(campaigns, key=lambda c: c.roi, reverse=True):
            if copy_count >= self.config["daily_copy_limit"]:
                break

            if account.target_roi > 0 and camp.roi > account.target_roi * 1.2:
                if account.target_cpl > 0 and camp.cpl < account.target_cpl * 0.7:
                    action = OptimizationAction(
                        action_id=f"COPY-{int(time.time())}-{camp.campaign_id[-4:]}",
                        action_type="copy",
                        platform=account.platform,
                        account_id=account.account_id,
                        campaign_id=camp.campaign_id,
                        reason=f"ROI={camp.roi} CPL=¥{camp.cpl}",
                        detail=f"优质计划复制: 保持配置 预算x0.6",
                        executed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    actions.append(action)
                    copy_count += 1
                    self.daily_copy_count[today_key] = copy_count
                    self.actions.append(action)

        return actions

    def check_and_adjust_bid(self, campaigns: List[CampaignMetrics],
                             account: AccountConfig) -> List[OptimizationAction]:
        """出价微调"""
        actions = []
        today_key = f"{account.platform}:{account.account_id}"

        for camp in campaigns:
            bid_count = self.bid_adjust_count.get(
                f"{today_key}:{camp.campaign_id}", 0
            )
            if bid_count >= self.config["bid_daily_limit"]:
                continue

            adjust_ratio = 0
            reason = ""

            if account.target_cpl > 0 and camp.cpl > account.target_cpl * 1.3:
                adjust_ratio = -0.08
                reason = f"CPL=¥{camp.cpl} 超目标,降价8%"
            elif account.target_cpl > 0 and camp.cpl < account.target_cpl * 0.7:
                if camp.impressions < 5000:
                    adjust_ratio = 0.05
                    reason = f"CPL=¥{camp.cpl} 低于目标,提价5%放量"
            elif camp.roi > 0 and account.target_roi > 0 and camp.roi < 0.5:
                adjust_ratio = -0.10
                reason = f"ROI={camp.roi} 过低,降价10%"

            if adjust_ratio != 0:
                new_bid = round(camp.bid * (1 + adjust_ratio), 2)
                success = self.adapters[account.platform].update_bid(
                    account.account_id, camp.campaign_id, new_bid
                )
                if success:
                    action = OptimizationAction(
                        action_id=f"BID-{int(time.time())}-{camp.campaign_id[-4:]}",
                        action_type="bid",
                        platform=account.platform,
                        account_id=account.account_id,
                        campaign_id=camp.campaign_id,
                        reason=reason,
                        detail=f"出价: ¥{camp.bid} → ¥{new_bid} ({adjust_ratio:+.0%})",
                        executed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    actions.append(action)
                    self.bid_adjust_count[f"{today_key}:{camp.campaign_id}"] = bid_count + 1
                    self.actions.append(action)

        return actions

    def _is_new_campaign(self, campaign: CampaignMetrics) -> bool:
        """判断是否为新计划（保护期内）"""
        try:
            created = datetime.strptime(campaign.created_time, "%Y-%m-%d %H:%M:%S")
            age_min = (datetime.now() - created).total_seconds() / 60
            return age_min < self.config["new_campaign_protection_min"]
        except Exception:
            return False

    def _stop_campaign(self, account: AccountConfig, campaign_id: str) -> bool:
        adapter = self.adapters.get(account.platform)
        if adapter:
            return adapter.update_status(account.account_id, campaign_id, "已暂停")
        return False


# ============================================================
# 告警推送器
# ============================================================

class AlertDispatcher:
    """告警消息推送"""

    def __init__(self, webhooks: Dict[str, str] = None):
        self.webhooks = webhooks or {}
        self.sent_alert_ids: set = set()
        self.alert_cooldown_min: Dict[str, datetime] = {}

    def dispatch(self, alerts: List[AlertEvent], account: AccountConfig):
        """按等级分发告警"""
        for alert in alerts:
            if alert.alert_id in self.sent_alert_ids:
                continue

            # 冷却检查 (同类型告警5分钟内不重复)
            cooldown_key = f"{account.platform}:{alert.alert_type}"
            if cooldown_key in self.alert_cooldown_min:
                elapsed = (datetime.now() - self.alert_cooldown_min[cooldown_key]).total_seconds()
                if elapsed < 300:
                    continue

            self._print_alert(alert)
            self.sent_alert_ids.add(alert.alert_id)
            self.alert_cooldown_min[cooldown_key] = datetime.now()

    def _print_alert(self, alert: AlertEvent):
        """控制台输出告警（对接企微/钉钉 SDK 可扩展）"""
        emoji = {
            AlertLevel.P0_EMERGENCY: "\U0001f534",
            AlertLevel.P1_HIGH: "\U0001f7e0",
            AlertLevel.P2_WARNING: "\U0001f7e1",
            AlertLevel.P3_INFO: "\U0001f535",
        }.get(alert.level, "\u26aa")
        print(f"\n{emoji} [{alert.level.value}] {alert.alert_type}")
        print(f"   平台: {alert.platform} | 账户: {alert.account_name}")
        print(f"   消息: {alert.alert_message}")
        print(f"   建议: {alert.action_taken}")
        print(f"   时间: {alert.triggered_at}")


# ============================================================
# 报告生成器
# ============================================================

class ReportGenerator:
    """投放报告生成"""

    @staticmethod
    def generate_daily(accounts: List[AccountConfig],
                       all_campaigns: Dict[str, List[CampaignMetrics]],
                       alerts: List[AlertEvent],
                       actions: List[OptimizationAction],
                       date: str = None) -> DailyReport:
        """生成日报"""
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        report = DailyReport(report_date=date)

        platform_data = {}
        for platform, campaigns in all_campaigns.items():
            p_cost = sum(c.cost for c in campaigns)
            p_imp = sum(c.impressions for c in campaigns)
            p_clicks = sum(c.clicks for c in campaigns)
            p_conv = sum(c.conversions for c in campaigns)
            p_gmv = sum(c.gmv for c in campaigns)

            report.total_cost += p_cost
            report.total_impressions += p_imp
            report.total_clicks += p_clicks
            report.total_conversions += p_conv
            report.total_gmv += p_gmv

            platform_data[platform] = {
                "cost": round(p_cost, 2),
                "impressions": p_imp,
                "clicks": p_clicks,
                "ctr": round(p_clicks / p_imp * 100, 2) if p_imp > 0 else 0,
                "cpc": round(p_cost / p_clicks, 2) if p_clicks > 0 else 0,
                "conversions": p_conv,
                "cvr": round(p_conv / p_clicks * 100, 2) if p_clicks > 0 else 0,
                "cpl": round(p_cost / p_conv, 2) if p_conv > 0 else 0,
                "gmv": round(p_gmv, 2),
                "roi": round(p_gmv / p_cost, 2) if p_cost > 0 else 0,
                "campaign_count": len(campaigns),
            }

        report.platform_breakdown = platform_data
        report.alerts_today = alerts
        report.actions_today = actions

        # 计算均值
        if report.total_impressions > 0:
            report.avg_ctr = round(report.total_clicks / report.total_impressions * 100, 2)
            report.avg_cpc = round(report.total_cost / report.total_clicks, 2) if report.total_clicks > 0 else 0
        if report.total_conversions > 0:
            report.avg_cpl = round(report.total_cost / report.total_conversions, 2)
        if report.total_cost > 0:
            report.avg_roi = round(report.total_gmv / report.total_cost, 2)

        # 生成优化建议
        suggestions = []
        for platform, data in platform_data.items():
            if data["roi"] < 1.5:
                suggestions.append(
                    f"{platform} ROI={data['roi']} 偏低，建议缩减预算或排查转化链路"
                )
            if data["cpl"] > 150:
                suggestions.append(
                    f"{platform} CPL=¥{data['cpl']} 偏高，建议优化定向和素材"
                )
        if not suggestions:
            suggestions.append("今日整体投放表现良好，建议保持当前策略")
        report.suggestions = suggestions

        return report

    @staticmethod
    def format_daily_report(report: DailyReport) -> str:
        """格式化为文本报告"""
        sep = "━" * 55
        lines = [sep, " Kevy 投放优化日志", f" 日期: {report.report_date}", sep, ""]

        # 大盘概览
        lines.append(" ┌─ 全域大盘 ──────────────────────────────────────")
        lines.append(f" │ 总消耗:        ¥{report.total_cost:,.2f}")
        lines.append(f" │ 总展现:        {report.total_impressions:,}")
        lines.append(f" │ 总点击:        {report.total_clicks:,}")
        lines.append(f" │ 平均CTR:       {report.avg_ctr}%")
        lines.append(f" │ 平均CPC:       ¥{report.avg_cpc}")
        lines.append(f" │ 总转化数:      {report.total_conversions:,}")
        lines.append(f" │ 综合CPL:       ¥{report.avg_cpl}")
        lines.append(f" │ 总GMV:         ¥{report.total_gmv:,.2f}")
        lines.append(f" │ 综合ROI:       {report.avg_roi}")
        lines.append(" └─────────────────────────────────────────────────")
        lines.append("")

        # 分平台
        lines.append(" ┌─ 分平台数据 ────────────────────────────────────")
        header = f" │ {'平台':<10} | {'消耗':>10} | {'转化':>6} | {'CPL':>8} | {'ROI':>5} | {'占比':>6}"
        lines.append(header)
        lines.append(" │ " + "─" * 55)
        for platform, data in sorted(
            report.platform_breakdown.items(),
            key=lambda x: x[1]["cost"], reverse=True
        ):
            ratio = data["cost"] / report.total_cost * 100 if report.total_cost > 0 else 0
            lines.append(
                f" │ {platform:<10} | ¥{data['cost']:>8,.2f} | {data['conversions']:>4} | "
                f"¥{data['cpl']:>6.2f} | {data['roi']:>4.1f} | {ratio:>5.1f}%"
            )
        lines.append(" └─────────────────────────────────────────────────")
        lines.append("")

        # 操作记录
        if report.actions_today:
            lines.append(" ┌─ 自动优化操作 ────────────────────────────────")
            stops = [a for a in report.actions_today if a.action_type == "stop"]
            copies = [a for a in report.actions_today if a.action_type == "copy"]
            bids = [a for a in report.actions_today if a.action_type == "bid"]
            if stops:
                lines.append(f" │ 关停低效计划:     {len(stops)} 条")
                for a in stops[:3]:
                    lines.append(f" │  └─ {a.campaign_id}: {a.reason}")
            if copies:
                lines.append(f" │ 优质计划复制:     {len(copies)} 条")
            if bids:
                lines.append(f" │ 出价微调:         {len(bids)} 次")
            lines.append(" └─────────────────────────────────────────────────")
            lines.append("")

        # 告警
        if report.alerts_today:
            lines.append(" ┌─ 异常告警记录 ────────────────────────────────")
            for a in report.alerts_today[:5]:
                lines.append(f" │ {a.alert_type:<8} | {a.platform} | {a.alert_message[:50]}")
            lines.append(" └─────────────────────────────────────────────────")
            lines.append("")

        # 优化建议
        lines.append(" ┌─ 明日优化建议 ──────────────────────────────────")
        for i, s in enumerate(report.suggestions, 1):
            lines.append(f" │ {i}. {s}")
        lines.append(" └─────────────────────────────────────────────────")
        lines.append("")
        lines.append(sep)

        return "\n".join(lines)


# ============================================================
# 主控制器
# ============================================================

class TrafficSentinel:
    """Kevy 投流哨兵主控制器"""

    def __init__(self, config_path: str = None):
        self.accounts: List[AccountConfig] = []
        self.adapters: Dict[str, PlatformAdapter] = {}
        self.detector = AnomalyDetector()
        self.dispatcher = AlertDispatcher()
        self._load_config(config_path)

    def _load_config(self, path: str = None):
        """加载配置"""
        if not path:
            path = os.path.join(os.path.dirname(__file__), "kevy_config.yaml")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            for acc in config.get("accounts", []):
                self.accounts.append(AccountConfig(**acc))
            for p, creds in config.get("credentials", {}).items():
                self.adapters[p] = MockPlatformAdapter(p, creds)
            webhooks = config.get("webhooks", {})
            self.dispatcher = AlertDispatcher(webhooks)
            print(f"[Kevy] 已加载 {len(self.accounts)} 个账户, {len(self.adapters)} 个平台")
        else:
            # 默认配置
            self.accounts = [
                AccountConfig("巨量引擎", "acc_ocean_001", "测试教育账户",
                              "教育", 50000, 500000, 100, 3.0),
            ]
            self.adapters["巨量引擎"] = MockPlatformAdapter("巨量引擎", {})
            print("[Kevy] 使用默认配置（测试模式）")

    def refresh_all_tokens(self):
        """刷新所有平台的 token"""
        for name, adapter in self.adapters.items():
            if adapter.refresh_token():
                print(f"  [Kevy] {name} token 已刷新")

    def run_once(self) -> Tuple[Dict, List[AlertEvent], List[OptimizationAction]]:
        """单次全量检查"""
        print(f"\n[Kevy] 开始全量检查 ({datetime.now().strftime('%H:%M:%S')})")
        all_campaigns = {}
        all_alerts = []
        all_actions = []

        for account in self.accounts:
            adapter = self.adapters.get(account.platform)
            if not adapter or not account.is_active:
                continue

            # 拉取数据
            campaigns = adapter.fetch_campaigns(account.account_id)
            all_campaigns[account.platform] = campaigns

            # KPI 计算
            for camp in campaigns:
                KPICalculator.calculate(camp)

            # 异常检测
            for camp in campaigns:
                alerts = self.detector.detect(camp, account)
                all_alerts.extend(alerts)

            # 推送告警
            self.dispatcher.dispatch(all_alerts, account)

            # 自动优化
            optimizer = AutoOptimizer(self.adapters)
            stop_actions = optimizer.check_and_stop_inefficient(campaigns, account)
            copy_actions = optimizer.check_and_copy_high_performing(campaigns, account)
            bid_actions = optimizer.check_and_adjust_bid(campaigns, account)

            all_actions.extend(stop_actions + copy_actions + bid_actions)

        summary = {
            "accounts_scanned": len(self.accounts),
            "campaigns_found": sum(len(c) for c in all_campaigns.values()),
            "alerts_triggered": len(all_alerts),
            "actions_executed": len(all_actions),
        }
        print(f"[Kevy] 扫描完成: {json.dumps(summary, ensure_ascii=False)}")
        return all_campaigns, all_alerts, all_actions

    def run_cycle(self, interval_min: int = 5):
        """循环监控模式"""
        print(f"\n{'='*55}")
        print(" Kevy 投流哨兵 已启动")
        print(f" 扫描间隔: {interval_min}分钟")
        print(f" 监控账户: {len(self.accounts)} 个")
        print(f" 监控平台: {list(self.adapters.keys())}")
        print(f"{'='*55}")

        while True:
            try:
                self.run_once()
                print(f"[Kevy] 等待 {interval_min} 分钟后下次扫描...")
                time.sleep(interval_min * 60)
            except KeyboardInterrupt:
                print("\n[Kevy] 监控已停止")
                break
            except Exception as e:
                print(f"[Kevy] 错误: {e}")
                time.sleep(60)

    def generate_report(self) -> str:
        """生成现状报告"""
        campaigns, alerts, actions = self.run_once()
        report = ReportGenerator.generate_daily(
            self.accounts, campaigns, alerts, actions
        )
        return ReportGenerator.format_daily_report(report)


# ============================================================
# 入口
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Kevy 投流哨兵 - 全域投放优化工具")
    parser.add_argument("--mode", "-m", choices=["monitor", "once", "report", "interactive"],
                       default="once", help="运行模式")
    parser.add_argument("--interval", "-i", type=int, default=5, help="监控间隔(分钟)")
    parser.add_argument("--config", "-c", help="配置文件路径")
    args = parser.parse_args()

    sentinel = TrafficSentinel(args.config)

    if args.mode == "monitor":
        sentinel.run_cycle(args.interval)
    elif args.mode == "once":
        sentinel.run_once()
    elif args.mode == "report":
        report = sentinel.generate_report()
        print(report)
        report_path = f"kevy_report_{datetime.now().strftime('%Y%m%d')}.txt"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[Kevy] 报告已保存: {report_path}")
    elif args.mode == "interactive":
        print("=" * 55)
        print(" Kevy 投流哨兵 - 交互模式")
        print("=" * 55)
        print(" 可用命令: scan / report / status / exit")
        while True:
            cmd = input("\n Kevy> ").strip().lower()
            if cmd in ("exit", "quit", "q"):
                break
            elif cmd == "scan":
                sentinel.run_once()
            elif cmd == "report":
                print(sentinel.generate_report())
            elif cmd == "status":
                print(f"  账户数: {len(sentinel.accounts)}")
                print(f"  平台数: {len(sentinel.adapters)}")
            else:
                print("  未知命令")


if __name__ == "__main__":
    main()
