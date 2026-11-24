import unittest

from src.services import analysis, reconcile

PARAMS = {"period": "全部时段", "min_observations": 2, "tau_threshold": 0.6, "queue_threshold": 25}


def check(route, day, time, queue):
    return {"route": route, "service_date": day, "check_time": time, "queue_people": queue}


def departure(route, day, time, headway):
    return {"route": route, "service_date": day, "departure_time": time, "headway_min": headway}


def decisions(result, route):
    for row in result["tables"][0]["rows"]:
        if row[0] == route:
            return row
    raise AssertionError(f"线路 {route} 不在判定表中")


class GroupPairingTest(unittest.TestCase):
    def test_tolerance_boundary(self):
        checks = [check("R", "2026-11-24", "07:00", 10)]
        inside = [departure("R", "2026-11-24", "06:58", 5), departure("R", "2026-11-24", "07:02", 6)]
        total, outcomes, _ = reconcile._group_outcomes(checks, inside)
        self.assertEqual(total, 2)
        self.assertEqual(len(outcomes), 2)
        outside = [departure("R", "2026-11-24", "06:57", 5), departure("R", "2026-11-24", "07:03", 6)]
        total, outcomes, _ = reconcile._group_outcomes(checks, outside)
        self.assertEqual(total, 0)
        self.assertEqual(outcomes, [])

    def test_order_is_preserved(self):
        # 两个清点都能配到两个班次，但顺序不可交换，只剩一种完整配对。
        checks = [check("R", "2026-11-24", "07:00", 10), check("R", "2026-11-24", "07:03", 20)]
        departures = [departure("R", "2026-11-24", "07:01", 5), departure("R", "2026-11-24", "07:02", 6)]
        total, outcomes, _ = reconcile._group_outcomes(checks, departures)
        self.assertEqual(total, 1)
        self.assertEqual([pair["departure_time"] for pair in outcomes[0]["pairs"]], ["07:01", "07:02"])

    def test_skips_unchecked_departures(self):
        checks = [check("R", "2026-11-24", "07:00", 10), check("R", "2026-11-24", "08:00", 20)]
        departures = [departure("R", "2026-11-24", "07:01", 5),
                      departure("R", "2026-11-24", "07:30", 6),
                      departure("R", "2026-11-24", "08:01", 7)]
        total, outcomes, _ = reconcile._group_outcomes(checks, departures)
        self.assertEqual(total, 1)
        self.assertEqual([pair["departure_time"] for pair in outcomes[0]["pairs"]], ["07:01", "08:01"])

    def test_distinct_outcomes_deduped(self):
        # 三套完整配对使用不同班次，观测各不相同，各留一套完整配对作证。
        checks = [check("R", "2026-11-24", "07:01", 26),
                  check("R", "2026-11-24", "07:05", 30),
                  check("R", "2026-11-24", "07:09", 34)]
        departures = [departure("R", "2026-11-24", "07:00", 20), departure("R", "2026-11-24", "07:03", 3),
                      departure("R", "2026-11-24", "07:06", 3), departure("R", "2026-11-24", "07:10", 4)]
        total, outcomes, overflow = reconcile._group_outcomes(checks, departures)
        self.assertEqual(total, 3)
        self.assertEqual(len(outcomes), 3)
        self.assertFalse(overflow)
        self.assertTrue(all(len(outcome["pairs"]) == 3 for outcome in outcomes))


class ReconcileTest(unittest.TestCase):
    def test_recommends_only_when_every_pairing_supports_pilot(self):
        checks = [check("R", "2026-11-24", "07:01", 20), check("R", "2026-11-24", "07:10", 40)]
        departures = [departure("R", "2026-11-24", "07:00", 8), departure("R", "2026-11-24", "07:09", 9)]
        result = reconcile.reconcile(checks, departures, PARAMS)
        row = decisions(result, "R")
        self.assertEqual(row[1], "已对上")
        self.assertEqual(row[2], "试排加班车")
        self.assertEqual(row[4], "推荐试排")

    def test_ambiguous_pairing_blocks_recommendation(self):
        # 同一批记录存在两套完整配对，一套支持试排、一套要求复核，则不推荐。
        checks = [check("R", "2026-11-24", "07:01", 20), check("R", "2026-11-24", "07:10", 40)]
        departures = [departure("R", "2026-11-24", "07:00", 8),
                      departure("R", "2026-11-24", "07:02", 30),
                      departure("R", "2026-11-24", "07:09", 9)]
        result = reconcile.reconcile(checks, departures, PARAMS)
        row = decisions(result, "R")
        self.assertEqual(row[1], "已对上")
        self.assertEqual(row[2], "试排加班车、复核客流与上客组织")
        self.assertEqual(row[3], 2)
        self.assertEqual(row[4], "暂不推荐")
        pairing_tables = [table for table in result["tables"] if table["title"].startswith("R · 判定「")]
        self.assertEqual(len(pairing_tables), 2)
        for table in pairing_tables:
            self.assertEqual(len(table["rows"]), 2)

    def test_unmatched_when_check_has_no_departure(self):
        checks = [check("R", "2026-11-24", "07:00", 10), check("R", "2026-11-24", "12:00", 20)]
        departures = [departure("R", "2026-11-24", "07:01", 8)]
        result = reconcile.reconcile(checks, departures, PARAMS)
        row = decisions(result, "R")
        self.assertEqual(row[1], "对不上")
        self.assertEqual(row[2], "2026-11-24")

    def test_checks_without_departure_group_are_unmatched(self):
        checks = [check("R", "2026-11-25", "07:00", 10)]
        departures = [departure("R", "2026-11-24", "07:00", 8)]
        result = reconcile.reconcile(checks, departures, PARAMS)
        self.assertEqual(decisions(result, "R")[1], "对不上")

    def test_period_filter_reuses_departure_time(self):
        checks = [check("R", "2026-11-24", "07:01", 30), check("R", "2026-11-24", "10:01", 40)]
        departures = [departure("R", "2026-11-24", "07:00", 8), departure("R", "2026-11-24", "10:00", 9)]
        params = {**PARAMS, "period": "早高峰", "min_observations": 2}
        result = reconcile.reconcile(checks, departures, params)
        # 早高峰只剩一条记录，样本不足，判定补采。
        self.assertEqual(decisions(result, "R")[2], "补采发车记录")

    def test_combines_dates_per_route(self):
        checks = [check("R", "2026-11-24", "07:01", 20), check("R", "2026-11-25", "07:01", 40)]
        departures = [departure("R", "2026-11-24", "07:00", 8), departure("R", "2026-11-25", "07:00", 9)]
        result = reconcile.reconcile(checks, departures, PARAMS)
        self.assertEqual(decisions(result, "R")[2], "试排加班车")
        coverage = result["tables"][2]["rows"]
        self.assertEqual(len(coverage), 2)

    def test_empty_source_rejected(self):
        with self.assertRaises(ValueError):
            reconcile.reconcile([], [departure("R", "2026-11-24", "07:00", 8)], PARAMS)
        with self.assertRaises(ValueError):
            reconcile.reconcile([check("R", "2026-11-24", "07:00", 10)], [], PARAMS)

    def test_overflow_when_pairings_explode(self):
        checks = [check("R", "2026-11-24", f"07:{index * 5:02d}", 10 + index) for index in range(5)]
        departures = [departure("R", "2026-11-24", f"06:{58 + index:02d}" if index < 2 else f"07:{index - 2:02d}", 1)
                      for index in range(25)]
        result = reconcile.reconcile(checks, departures, PARAMS)
        row = decisions(result, "R")
        self.assertEqual(row[1], "配对组合过多")
        self.assertEqual(row[4], "需人工复核")

    def test_route_only_in_departures_needs_resampling(self):
        checks = [check("R", "2026-11-24", "07:01", 20), check("R", "2026-11-24", "07:10", 40)]
        departures = [departure("R", "2026-11-24", "07:00", 8), departure("R", "2026-11-24", "07:09", 9),
                      departure("X", "2026-11-24", "08:00", 10)]
        result = reconcile.reconcile(checks, departures, PARAMS)
        self.assertEqual(decisions(result, "X")[2], "补采发车记录")


class SourceValidationTest(unittest.TestCase):
    def test_duplicate_minute_rejected(self):
        rows = [check("R", "2026-11-24", "07:00", 10), check("R", "2026-11-24", "07:00", 12)]
        with self.assertRaises(ValueError):
            reconcile.validate_source_rows(rows, "checks")

    def test_bad_time_rejected(self):
        with self.assertRaises(ValueError):
            reconcile.validate_source_rows([departure("R", "2026-11-24", "7:00", 8)], "departures")
        with self.assertRaises(ValueError):
            reconcile.validate_source_rows([check("R", "2026-11-24", "24:00", 10)], "checks")

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            reconcile.validate_source_rows([], "ledger")


class DetermineTest(unittest.TestCase):
    def test_threshold_ladder(self):
        params = {"period": "全部时段", "min_observations": 2, "tau_threshold": 0.6, "queue_threshold": 25}
        self.assertEqual(analysis.determine([], params)["action"], "补采发车记录")
        self.assertEqual(analysis.determine([(8, 10)], params)["action"], "补采发车记录")
        self.assertEqual(analysis.determine([(8, 10), (8, 40)], params)["action"], "补采有变化的班距或队列")
        self.assertEqual(analysis.determine([(8, 10), (9, 12)], params)["action"], "维持现行班次")
        self.assertEqual(analysis.determine([(8, 40), (9, 20)], params)["action"], "复核客流与上客组织")
        self.assertEqual(analysis.determine([(8, 20), (9, 40)], params)["action"], "试排加班车")


if __name__ == "__main__":
    unittest.main()
