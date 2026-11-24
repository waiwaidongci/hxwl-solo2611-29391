"""Route-level analysis of actual airport shuttle departures and queues."""

import math
import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import date
from itertools import combinations
from typing import NamedTuple


ALL_PERIODS = "全部时段"
MORNING_PEAK = "早高峰"
OTHER_PERIODS = "其他时段"
PILOT = "试排加班车"
REVIEW = "复核客流与上客组织"
MAINTAIN = "维持现行班次"


class PairCounts(NamedTuple):
    concordant: int
    discordant: int
    headway_only_ties: int
    queue_only_ties: int
    joint_ties: int

    def tau(self):
        ordered = self.concordant + self.discordant
        denominator = (ordered + self.headway_only_ties) * (ordered + self.queue_only_ties)
        if denominator == 0:
            return None
        return (self.concordant - self.discordant) / math.sqrt(denominator)


def _count_pairs(observations):
    concordant = discordant = headway_ties = queue_ties = joint_ties = 0
    for (headway, queue), (other_headway, other_queue) in combinations(observations, 2):
        if headway == other_headway:
            if queue == other_queue:
                joint_ties += 1
            else:
                headway_ties += 1
        elif queue == other_queue:
            queue_ties += 1
        elif (headway < other_headway) == (queue < other_queue):
            concordant += 1
        else:
            discordant += 1
    return PairCounts(concordant, discordant, headway_ties, queue_ties, joint_ties)


def _whole_number(value, label, minimum, maximum=None):
    if isinstance(value, bool):
        raise ValueError(f"{label}须为整数。")
    if isinstance(value, str):
        value = value.strip()
        if re.fullmatch(r"[+-]?[0-9]+", value):
            try:
                value = int(value)
            except ValueError as exc:
                raise ValueError(f"{label}的整数位数过多。") from exc
    elif isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int):
        raise ValueError(f"{label}须为整数。")
    if value < minimum:
        raise ValueError(f"{label}须至少为 {minimum}。")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label}须不超过 {maximum}。")
    return value


def _read_parameters(params):
    if not isinstance(params, Mapping):
        raise ValueError("分析参数须为键值表。")
    period = params.get("period", ALL_PERIODS)
    if period not in (ALL_PERIODS, MORNING_PEAK, OTHER_PERIODS):
        raise ValueError("发车时段须为全部时段、早高峰或其他时段。")
    minimum = _whole_number(params.get("min_observations", 8), "每条线路最少发车记录", 2)
    queue_threshold = _whole_number(params.get("queue_threshold", 25), "排队 P90 门槛", 1)
    tau_threshold = params.get("tau_threshold", 0.6)
    error = "正相关门槛须为 0.01 至 1 之间的有限数值。"
    if isinstance(tau_threshold, bool):
        raise ValueError(error)
    try:
        tau_threshold = float(tau_threshold)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(error) from exc
    if not 0.01 <= tau_threshold <= 1:
        raise ValueError(error)
    return period, minimum, tau_threshold, queue_threshold


def in_period(departure_time, period):
    observed_period = MORNING_PEAK if "06:00" <= departure_time < "09:00" else OTHER_PERIODS
    return period in (ALL_PERIODS, observed_period)


def _read_observations(rows, period):
    observations = defaultdict(list)
    seen_departures = set()
    for index, row in enumerate(rows, start=1):
        prefix = f"第 {index} 条台账记录"
        if not isinstance(row, Mapping):
            raise ValueError(f"{prefix}须为字段键值表。")
        route = row.get("route")
        if not isinstance(route, str) or not route.strip():
            raise ValueError(f"{prefix}的接驳线路不能为空。")
        route = route.strip()
        service_date = row.get("service_date")
        try:
            if date.fromisoformat(service_date).isoformat() != service_date:
                raise ValueError()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{prefix}的本地运行日期须为有效的 YYYY-MM-DD。") from exc
        departure_time = row.get("departure_time")
        if not isinstance(departure_time, str) or not re.fullmatch(
            r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", departure_time
        ):
            raise ValueError(f"{prefix}的发车时刻须为有效的 24 小时制 HH:MM。")
        headway = _whole_number(row.get("headway_min"), f"{prefix}的实际班距", 1, 1440)
        queue = _whole_number(row.get("queue_people"), f"{prefix}的候车人数", 0)
        departure = (route, service_date, departure_time)
        if departure in seen_departures:
            raise ValueError(f"{prefix}与已有记录重复（{route}，{service_date} {departure_time}）。")
        seen_departures.add(departure)
        if in_period(departure_time, period):
            observations[route].append((headway, queue))
    return observations


def _route_action(count, tau, p90, minimum, tau_threshold, queue_threshold):
    if count < minimum:
        return "补采发车记录"
    if tau is None:
        return "补采有变化的班距或队列"
    if p90 < queue_threshold:
        return MAINTAIN
    if tau < tau_threshold:
        return REVIEW
    return PILOT


def _summary(period, eligible, pilots, minimum, tau_threshold, queue_threshold):
    if pilots:
        route, count, p90, tau, _ = pilots[0]
        return (
            f"{period}有 {len(pilots)} 条线路达到试排条件。建议机动运力调度组优先为 {route} "
            f"在下一相似时段试排一辆加班车并复测（{count} 条，τ-b {tau:.4f}，候车 P90 {p90} 人）。"
        )
    if eligible:
        route, _, p90, tau, action = eligible[0]
        return (
            f"{period}无线路同时达到 τ-b {tau_threshold} 和候车 P90 {queue_threshold} 人的试排门槛。"
            f"在可判定的线路中，{route} 的排队 P90 最高（{p90} 人，τ-b {tau:.4f}），建议{action}。"
        )
    return (
        f"{period}没有可判定的线路。请按线路补采至至少 {minimum} 条记录，"
        "并覆盖有变化的班距与候车人数。"
    )


def read_parameters(params):
    """Validate and return (period, minimum, tau_threshold, queue_threshold)."""
    return _read_parameters(params)


def determine(observations, params):
    """Decide one route from its (headway, queue) observations under current thresholds."""
    _, minimum, tau_threshold, queue_threshold = _read_parameters(params)
    count = len(observations)
    if not count:
        return {"count": 0, "p90": None, "tau": None, "action": "补采发车记录"}
    queues = sorted(queue for _, queue in observations)
    p90 = queues[(9 * count + 9) // 10 - 1]
    tau = _count_pairs(observations).tau()
    return {"count": count, "p90": p90, "tau": tau,
            "action": _route_action(count, tau, p90, minimum, tau_threshold, queue_threshold)}


def analyze(rows, params):
    """Validate departure records and return a JSON-safe, route-stratified result."""
    period, minimum, tau_threshold, queue_threshold = _read_parameters(params)
    if not rows:
        raise ValueError("发车台账为空，请先导入实际发车记录。")
    grouped = _read_observations(rows, period)
    decision_rows = []
    pair_rows = []
    for route in sorted(grouped):
        observations = grouped[route]
        count = len(observations)
        queues = sorted(queue for _, queue in observations)
        p90 = queues[(9 * count + 9) // 10 - 1]
        pairs = _count_pairs(observations)
        tau = pairs.tau()
        action = _route_action(count, tau, p90, minimum, tau_threshold, queue_threshold)
        decision_rows.append((route, count, p90, tau, action))
        pair_rows.append([route, *pairs])

    by_pressure = sorted(decision_rows, key=lambda row: (-row[2], row[0]))
    eligible = [row for row in by_pressure if row[1] >= minimum and row[3] is not None]
    pilots = [row for row in eligible if row[4] == PILOT]
    if grouped:
        summary = _summary(period, eligible, pilots, minimum, tau_threshold, queue_threshold)
    else:
        summary = f"{period}没有发车记录。请切换时段或补录该时段台账。"
    return {
        "summary": summary,
        "metrics": [
            {"label": "入选发车记录", "value": sum(row[1] for row in decision_rows), "detail": period},
            {"label": "覆盖线路", "value": len(grouped), "detail": "线路内单独比较"},
            {"label": "可判定线路", "value": len(eligible), "detail": f"每条至少 {minimum} 条且系数有定义"},
            {"label": "试排加班车线路", "value": len(pilots), "detail": f"τ-b ≥ {tau_threshold} 且排队 P90 ≥ {queue_threshold} 人"},
        ],
        "tables": [
            {
                "title": "按线路调度判定",
                "columns": ["接驳线路", "发车记录（条）", "排队 P90（人）", "Kendall τ-b（4 位小数）", "下一班次动作"],
                "rows": [
                    [route, count, p90, "无定义" if tau is None else round(tau, 4), action]
                    for route, count, p90, tau, action in decision_rows
                ],
            },
            {
                "title": "线路内配对核对",
                "columns": ["接驳线路", "同序对", "逆序对", "仅班距并列", "仅队列并列", "双列并列"],
                "rows": pair_rows,
            },
        ],
        "notes": [
            "τ-b 按线路内的同序对、逆序对和两类单列并列计算；双列并列单列核对。仅一个值的变量使 τ-b 无定义。",
            "排队 P90 取最近秩。判定使用未舍入的 τ-b，样本不足时保留可计算的系数供补采对照。",
            "图示为排队 P90（人），按排队人数取前 12 条线路，同值按线路名称排序。",
            "客流波次、车型容量、临时管制和调度响应都可能影响关联。建议用相似时段的复测评价试排效果。",
        ],
        "series": [{"label": route, "value": p90} for route, _, p90, _, _ in by_pressure[:12]],
    }
