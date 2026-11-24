"""Cross-system reconciliation of headcount checks and departure records.

候车清点与发车记录来自两个系统，时钟对不上。对账按线路和日期分开，按清点顺序
把每条清点配到前后两分钟内的不同班次，允许跳过未清点的班次。判定沿用现有的
发车时段与样本、正相关、排队门槛：枚举每条线路所有完整配对组合，列出所有可
能的判定并各附一套完整配对；只有每套完整配对都支持试排加班车时才推荐试排，
任一日期配不出完整配对时报对不上。
"""

import re
from collections import defaultdict
from datetime import date

from src.services import analysis

CHECKS = "checks"
DEPARTURES = "departures"
TOLERANCE_MIN = 2
MAX_GROUP_OUTCOMES = 100
MAX_ROUTE_OUTCOMES = 400
MAX_SEARCH_STEPS = 200_000

UNMATCHED = "对不上"
OVERFLOW = "配对组合过多"
ACTION_ORDER = [analysis.PILOT, analysis.REVIEW, analysis.MAINTAIN, "补采发车记录", "补采有变化的班距或队列"]

_TIME_PATTERN = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")


def validate_source_rows(rows, kind):
    """导入时校验候车清点或发车记录批次，同一线路同一日期同一分钟只录一条。"""
    if kind == CHECKS:
        label, time_key = "候车清点", "check_time"
    elif kind == DEPARTURES:
        label, time_key = "发车记录", "departure_time"
    else:
        raise ValueError("未知的数据批次类型。")
    seen = set()
    for index, row in enumerate(rows, start=1):
        prefix = f"第 {index} 条{label}记录"
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
        moment = row.get(time_key)
        if not isinstance(moment, str) or not _TIME_PATTERN.fullmatch(moment):
            raise ValueError(f"{prefix}的时刻须为有效的 24 小时制 HH:MM。")
        key = (route, service_date, moment)
        if key in seen:
            raise ValueError(f"{prefix}与已有记录重复（{route}，{service_date} {moment}）。")
        seen.add(key)


def _minutes(text):
    return int(text[:2]) * 60 + int(text[3:])


def _grouped(rows, time_key):
    groups = defaultdict(list)
    for row in rows:
        groups[(str(row.get("route") or "").strip(), row.get("service_date"))].append(row)
    for items in groups.values():
        items.sort(key=lambda row: _minutes(row[time_key]))
    return groups


def _pairing_count(check_minutes, departure_minutes):
    """Exact count of order-preserving complete pairings (dynamic programming)."""
    matched = [0] * (len(check_minutes) + 1)
    matched[0] = 1
    for departure in departure_minutes:
        for index in range(len(check_minutes), 0, -1):
            if abs(check_minutes[index - 1] - departure) <= TOLERANCE_MIN:
                matched[index] += matched[index - 1]
    return matched[len(check_minutes)]


def _group_outcomes(checks, departures):
    """Enumerate distinct observation outcomes of complete pairings for one route+date.

    Returns (total, outcomes, overflow): the exact number of complete pairings,
    one witness pairing per distinct observation set (capped), and whether the
    distinct outcomes exceeded the cap so the route can no longer be certified.
    """
    check_minutes = [_minutes(row["check_time"]) for row in checks]
    departure_minutes = [_minutes(row["departure_time"]) for row in departures]
    total = _pairing_count(check_minutes, departure_minutes)
    outcomes = {}
    steps = 0
    overflow = False
    chosen = []

    def search(index, cursor):
        nonlocal steps, overflow
        if overflow:
            return
        steps += 1
        if steps > MAX_SEARCH_STEPS:
            overflow = True
            return
        if index == len(checks):
            observations = tuple(
                (departures[pick]["departure_time"], departures[pick]["headway_min"],
                 checks[offset]["queue_people"])
                for offset, pick in enumerate(chosen)
            )
            if observations not in outcomes:
                if len(outcomes) >= MAX_GROUP_OUTCOMES:
                    overflow = True
                    return
                outcomes[observations] = [
                    {"service_date": checks[offset]["service_date"],
                     "check_time": checks[offset]["check_time"],
                     "departure_time": departures[pick]["departure_time"],
                     "headway_min": departures[pick]["headway_min"],
                     "queue_people": checks[offset]["queue_people"]}
                    for offset, pick in enumerate(chosen)
                ]
            return
        remaining = len(checks) - index
        for pick in range(cursor, len(departures) - remaining + 1):
            if departure_minutes[pick] > check_minutes[index] + TOLERANCE_MIN:
                break
            if abs(check_minutes[index] - departure_minutes[pick]) > TOLERANCE_MIN:
                continue
            chosen.append(pick)
            search(index + 1, pick + 1)
            chosen.pop()
            if overflow:
                return

    search(0, 0)
    witnesses = [{"observations": list(key), "pairs": pairs} for key, pairs in outcomes.items()]
    return total, witnesses, overflow


def _combine(dated_outcomes):
    """Combine per-date outcomes into route-level observation sets (capped)."""
    combined = {(): {"observations": [], "pairs": []}}
    overflow = False
    for service_date, outcomes in dated_outcomes:
        merged = {}
        for key, acc in combined.items():
            for outcome in outcomes:
                new_key = tuple(sorted(key + tuple((service_date,) + item for item in outcome["observations"])))
                if new_key not in merged:
                    if len(merged) >= MAX_ROUTE_OUTCOMES:
                        overflow = True
                        break
                    merged[new_key] = {"observations": acc["observations"] + outcome["observations"],
                                       "pairs": acc["pairs"] + outcome["pairs"]}
            if overflow:
                break
        if overflow:
            break
        combined = merged
    return list(combined.values()), overflow


def _route_report(route, dates, check_groups, departure_groups, period, params):
    coverage = []
    dated_outcomes = []
    unmatched = []
    overflow = False
    for service_date in dates:
        checks = check_groups.get((route, service_date), [])
        departures = departure_groups.get((route, service_date), [])
        if not checks:
            dated_outcomes.append((service_date, [{"observations": [], "pairs": []}]))
            coverage.append([route, service_date, 0, len(departures), "—", len(departures), "—"])
            continue
        total, outcomes, capped = _group_outcomes(checks, departures)
        skipped = len(departures) - len(checks) if total else "—"
        coverage.append([route, service_date, len(checks), len(departures), total, skipped,
                         f"{len(outcomes)} 种" if not capped else f"超过 {MAX_GROUP_OUTCOMES} 种"])
        if not total:
            unmatched.append(service_date)
        overflow = overflow or capped
        dated_outcomes.append((service_date, outcomes))
    if unmatched:
        return {"route": route, "state": "unmatched", "unmatched_dates": unmatched}, coverage
    combinations, combined_overflow = _combine(dated_outcomes)
    if overflow or combined_overflow:
        return {"route": route, "state": "overflow"}, coverage
    actions = {}
    stats = {}
    for combination in combinations:
        observations = [(headway, queue) for departure_time, headway, queue in combination["observations"]
                        if analysis.in_period(departure_time, period)]
        outcome = analysis.determine(observations, params)
        actions.setdefault(outcome["action"], combination["pairs"])
        stats.setdefault(outcome["action"], {**outcome, "combinations": 0})
        stats[outcome["action"]]["combinations"] += 1
    return {"route": route, "state": "resolved", "actions": actions, "stats": stats,
            "combinations": len(combinations),
            "recommended": set(actions) == {analysis.PILOT}}, coverage


def _summary(reports, recommended, period):
    unmatched = [item["route"] for item in reports if item["state"] == "unmatched"]
    overflow = [item["route"] for item in reports if item["state"] == "overflow"]
    if recommended:
        headline = (f"{period}跨系统对账完成：{'、'.join(item['route'] for item in recommended)} "
                    "在所有完整配对下均支持试排加班车，可在下一相似时段试排一辆并复测。")
    elif any(item["state"] == "resolved" for item in reports):
        headline = f"{period}跨系统对账完成：没有线路在所有完整配对下都支持试排加班车，暂不推荐试排。"
    else:
        headline = f"{period}跨系统对账没有可判定的线路。"
    if unmatched:
        headline += f" {'、'.join(unmatched)} 对不上，请核对两个系统的原始记录。"
    if overflow:
        headline += f" {'、'.join(overflow)} 的配对组合过多，请人工复核后再判定。"
    return headline


def reconcile(check_rows, departure_rows, params):
    """Reconcile headcount checks with departure records and decide per route."""
    period, minimum, tau_threshold, queue_threshold = analysis.read_parameters(params)
    if not check_rows:
        raise ValueError("候车清点为空，请先导入清点记录批次。")
    if not departure_rows:
        raise ValueError("发车记录为空，请先导入发车记录批次。")
    check_groups = _grouped(check_rows, "check_time")
    departure_groups = _grouped(departure_rows, "departure_time")
    routes = sorted({route for route, _ in check_groups} | {route for route, _ in departure_groups})
    reports = []
    coverage = []
    for route in routes:
        dates = sorted({day for name, day in check_groups if name == route}
                       | {day for name, day in departure_groups if name == route})
        report, rows = _route_report(route, dates, check_groups, departure_groups, period, params)
        reports.append(report)
        coverage.extend(rows)
    recommended = [item for item in reports if item.get("recommended")]
    decision_rows = []
    detail_rows = []
    pairing_tables = []
    for report in reports:
        route = report["route"]
        if report["state"] == "unmatched":
            decision_rows.append([route, UNMATCHED, "、".join(report["unmatched_dates"]), "—", "—"])
            continue
        if report["state"] == "overflow":
            decision_rows.append([route, OVERFLOW, "—", "—", "需人工复核"])
            continue
        ordered = [action for action in ACTION_ORDER if action in report["actions"]]
        decision_rows.append([route, "已对上", "、".join(ordered), report["combinations"],
                              "推荐试排" if report["recommended"] else "暂不推荐"])
        for action in ordered:
            stat = report["stats"][action]
            detail_rows.append([route, action, stat["count"],
                                "无定义" if stat["p90"] is None else stat["p90"],
                                "无定义" if stat["tau"] is None else round(stat["tau"], 4),
                                stat["combinations"]])
            pairs = report["actions"][action]
            if pairs:
                pairing_tables.append({
                    "title": f"{route} · 判定「{action}」的完整配对",
                    "columns": ["运行日期", "清点时刻", "配对发车时刻", "实际班距（分钟）", "候车人数"],
                    "rows": [[pair["service_date"], pair["check_time"], pair["departure_time"],
                              pair["headway_min"], pair["queue_people"]] for pair in pairs],
                })
    unmatched_count = sum(1 for item in reports if item["state"] == "unmatched")
    overflow_count = sum(1 for item in reports if item["state"] == "overflow")
    return {
        "summary": _summary(reports, recommended, period),
        "metrics": [
            {"label": "对账线路", "value": len(reports), "detail": "按线路和日期分开对账"},
            {"label": "推荐试排加班车", "value": len(recommended), "detail": "每套完整配对都支持试排才推荐"},
            {"label": "对不上线路", "value": unmatched_count, "detail": "存在配不出完整配对的日期"},
            {"label": "配对组合过多", "value": overflow_count,
             "detail": f"单组超过 {MAX_GROUP_OUTCOMES} 种或线路超过 {MAX_ROUTE_OUTCOMES} 种"},
        ],
        "tables": [
            {"title": "按线路对账判定",
             "columns": ["接驳线路", "对账结果", "所有可能的判定", "完整配对组合（种）", "试排建议"],
             "rows": decision_rows},
            {"title": "判定统计明细",
             "columns": ["接驳线路", "可能的判定", "样本数（条）", "排队 P90（人）",
                         "Kendall τ-b（4 位小数）", "支持组合（种）"],
             "rows": detail_rows},
            {"title": "按线路和日期对账覆盖",
             "columns": ["接驳线路", "运行日期", "清点记录（条）", "发车记录（条）",
                         "完整配对（套）", "跳过班次（班）", "不同配对结果"],
             "rows": coverage},
            *pairing_tables,
        ],
        "notes": [
            f"对账按线路和日期分开，按清点顺序把每条清点配到前后 {TOLERANCE_MIN} 分钟内的不同班次，"
            "允许跳过未清点的班次；同一线路同一日期的清点与发车分钟只录一条。",
            f"判定沿用现有发车时段与门槛：每条至少 {minimum} 条、τ-b ≥ {tau_threshold}、"
            f"候车 P90 ≥ {queue_threshold} 人；只有每套完整配对都支持试排加班车时才推荐试排。",
            "任一日期配不出完整配对时报对不上；配对组合超过上限时无法穷尽，需人工复核，不自动推荐。",
            "补录数据另成批次，不影响已接单的运行；计算中断后恢复沿用接单时的两份记录和门槛。",
        ],
        "series_title": "各线路完整配对组合数",
        "series": [{"label": item["route"], "value": item["combinations"]}
                   for item in reports if item["state"] == "resolved"],
    }
