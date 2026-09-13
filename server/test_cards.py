#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`server/cards.py` —— 称号卡片该发谁几张（用户 2026-09-13，V0.3商店）。

★ 这一组里最要紧的是 `TotalScopeTests`：「玩家累计」用的是**里程碑**语义
（每满一个阈值给一次），三条性质缺一不可 ——

    幂等          同一份统计再算一遍发 0 张
    调高不倒扣    已经发出去的留在玩家仓库里
    调低会补齐    欠的账在下一局一次性还清

这三条都是「错了也不会报错、只会在几个月后变成一堆解释不清的卡片」的那一类。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cards                                                   # noqa: E402
import shopcfg                                                 # noqa: E402
import shopdefaults                                            # noqa: E402


def rule(card, **kw):
    """一条规则，只写关心的那几格，其余用 validator 的默认值。"""
    out = {"card": card, "listed": True, "scope": shopcfg.CARD_SCOPE_MATCH,
           "metric": "kills", "threshold": 1, "win_only": False,
           "count": 1, "limit": 0}
    out.update(kw)
    return out


def grants(rules, *, mode="pvp", stage=None, difficulty=None, won=True,
           match=None, total=None, granted=None):
    give, targets, warnings = cards.due_grants(
        rules, mode=mode, stage=stage, difficulty=difficulty, won=won,
        match=match or {}, total=total or {}, granted=granted or {})
    return give, targets, warnings


class MetricTableTests(unittest.TestCase):
    """指标表本身要自洽 —— 它是画面、校验器和判定三处共同的契约。"""

    def test_every_metric_has_a_chinese_name_and_a_sane_shape(self):
        for key, spec in shopcfg.CARD_METRICS.items():
            label, modes, weapon, ratio, help_text = spec
            self.assertTrue(label, key)
            self.assertTrue(help_text, key)
            self.assertIsInstance(weapon, bool, key)
            self.assertIsInstance(ratio, bool, key)
            for mode in modes:
                self.assertIn(mode, shopcfg.CARD_MODES, key)

    def test_the_flag_metrics_are_all_real_metrics(self):
        for key in shopcfg.CARD_FLAG_METRICS:
            self.assertIn(key, shopcfg.CARD_METRICS, key)

    def test_the_dropdown_order_covers_every_metric_exactly_once(self):
        """★ 下拉不是字母序（按 key 排在中文下是乱的）——
        漏登记一个指标不该让它从画面上消失，所以兜底也要验。"""
        keys = shopcfg.card_metric_keys()
        self.assertEqual(sorted(shopcfg.CARD_METRICS), sorted(keys))
        self.assertEqual(len(keys), len(set(keys)))
        for key in shopcfg.CARD_METRIC_ORDER:
            self.assertIn(key, shopcfg.CARD_METRICS, key)

    def test_the_weapon_metrics_say_so(self):
        """带武器的指标名里要有「某武器」—— `describe_card_rule` 靠替换它念人话。"""
        for key, spec in shopcfg.CARD_METRICS.items():
            if spec[2] and key.startswith("weapon_"):
                self.assertIn("某武器", spec[0], key)

    def test_the_weapon_cards_are_the_nine_weapon_families(self):
        import weapondata
        self.assertEqual(tuple(weapondata.WEAPON_ROH), shopcfg.WEAPON_CARDS)
        for roh in shopcfg.WEAPON_CARDS:
            self.assertIn(roh, shopcfg.WEAPON_ROH_ZH)

    def test_the_weapon_names_match_the_design_table(self):
        """武器族的中文名和 `shopdefaults.WEAPON_BASE_ZH` 那一份是同一套说法。"""
        wanted = set(shopdefaults.WEAPON_BASE_ZH.values())
        for name in shopcfg.WEAPON_ROH_ZH.values():
            self.assertIn(name, wanted, name)


class StatKeyTests(unittest.TestCase):

    def test_the_weapon_dimension_folds_into_the_key(self):
        self.assertEqual("kills", cards.stat_key("kills"))
        self.assertEqual("kills@110001", cards.stat_key("kills", 110001))

    def test_no_mode_means_both_modes_added_up(self):
        stats = {"pvp": {"kills": 3}, "quest": {"kills": 4}}
        self.assertEqual(3, cards.stat_of(stats, "pvp", "kills"))
        self.assertEqual(7, cards.stat_of(stats, None, "kills"))

    def test_accuracy_is_computed_not_stored(self):
        """★ 比率不许存：两局 50% 和 100% 加起来会变成 150%。"""
        stats = {"pvp": {"shots": 40, "hits": 10}}
        self.assertEqual(25, cards.stat_of(stats, "pvp", "accuracy"))

    def test_accuracy_with_no_shots_is_zero_not_a_crash(self):
        self.assertEqual(0, cards.stat_of({}, "pvp", "accuracy"))

    def test_merge_only_keeps_non_zero(self):
        merged = cards.merge_stats({"pvp": {"kills": 2, "deaths": 0}},
                                   "pvp", {"kills": 3, "guards": 0})
        self.assertEqual({"pvp": {"kills": 5}}, merged)

    def test_merge_does_not_touch_the_input(self):
        before = {"pvp": {"kills": 2}}
        cards.merge_stats(before, "pvp", {"kills": 3})
        self.assertEqual({"pvp": {"kills": 2}}, before)


class MatchScopeTests(unittest.TestCase):
    """「一局内」：达标就给，一局一次机会。"""

    def test_reaching_the_threshold_gives_the_cards(self):
        give, _t, _w = grants([rule(60001, metric="guards", threshold=30,
                                    count=2)],
                              match={"guards": 30})
        self.assertEqual({60001: 2}, give)

    def test_one_short_gives_nothing(self):
        give, _t, _w = grants([rule(60001, metric="guards", threshold=30)],
                              match={"guards": 29})
        self.assertEqual({}, give)

    def test_a_rule_that_is_switched_off_never_fires(self):
        """★ 这是运营的急刹车：17 张全关掉 = 整个功能停掉，不用重启不用发版。"""
        give, _t, _w = grants([rule(60001, listed=False, threshold=1)],
                              match={"kills": 99})
        self.assertEqual({}, give)

    def test_the_wrong_mode_never_fires(self):
        give, _t, _w = grants([rule(60001, mode="quest")], mode="pvp",
                              match={"kills": 9})
        self.assertEqual({}, give)

    def test_no_mode_means_either_mode(self):
        for mode in ("pvp", "quest"):
            give, _t, _w = grants([rule(60001)], mode=mode,
                                  match={"kills": 9})
            self.assertEqual({60001: 1}, give, mode)

    def test_stage_and_difficulty_narrow_it_down(self):
        only = [rule(60001, mode="quest", stage=3, difficulty=2)]
        give, _t, _w = grants(only, mode="quest", stage=3, difficulty=2,
                              match={"kills": 1})
        self.assertEqual({60001: 1}, give)
        give, _t, _w = grants(only, mode="quest", stage=3, difficulty=1,
                              match={"kills": 1})
        self.assertEqual({}, give)

    def test_win_only_needs_the_win(self):
        only = [rule(60001, win_only=True)]
        self.assertEqual({}, grants(only, won=False, match={"kills": 5})[0])
        self.assertEqual({60001: 1},
                         grants(only, won=True, match={"kills": 5})[0])

    def test_min_shots_is_the_sample_floor_for_ratios(self):
        """开一枪中一枪也是 100% —— 「最少开枪数」就是拦这个的。"""
        only = [rule(60003, metric="accuracy", threshold=60, min_shots=20)]
        self.assertEqual({}, grants(only, match={"shots": 1, "hits": 1})[0])
        self.assertEqual({60003: 1},
                         grants(only, match={"shots": 20, "hits": 15})[0])

    def test_a_weapon_metric_reads_the_weapon_specific_counter(self):
        only = [rule(110001, metric="weapon_kills", weapon=110001,
                     threshold=5)]
        self.assertEqual({}, grants(only, match={"weapon_kills": 9})[0])
        self.assertEqual({110001: 1},
                         grants(only, match={"weapon_kills@110001": 5})[0])

    def test_the_lifetime_limit_caps_a_per_match_rule(self):
        only = [rule(60001, limit=3)]
        self.assertEqual({60001: 1},
                         grants(only, match={"kills": 1}, granted={"60001": 2})[0])
        self.assertEqual({}, grants(only, match={"kills": 1},
                                    granted={"60001": 3})[0])

    def test_an_unknown_metric_is_skipped_with_a_warning(self):
        give, _t, warnings = grants([rule(60001, metric="nonsense")],
                                    match={"kills": 9})
        self.assertEqual({}, give)
        self.assertTrue(warnings)


class TotalScopeTests(unittest.TestCase):
    """「玩家累计」：里程碑，每满一个阈值给一次（用户 2026-09-13 拍板）。"""

    def milestone(self, threshold=100, count=1, limit=0):
        return [rule(60001, scope=shopcfg.CARD_SCOPE_TOTAL, mode="pvp",
                     metric="kills", threshold=threshold, count=count,
                     limit=limit)]

    def test_crossing_a_milestone_pays_the_difference(self):
        give, targets, _w = grants(self.milestone(), total={"pvp": {"kills": 250}},
                                   granted={})
        self.assertEqual({60001: 2}, give)
        self.assertEqual({60001: 2}, targets)

    def test_it_is_idempotent(self):
        """★ 同一份统计再算一遍发 0 张 —— 这是整个设计的地基。"""
        give, targets, _w = grants(self.milestone(),
                                   total={"pvp": {"kills": 250}},
                                   granted={"60001": 2})
        self.assertEqual({}, give)
        self.assertEqual({60001: 2}, targets)

    def test_raising_the_threshold_never_takes_cards_back(self):
        """运营把 100 调成 1000：不再发新的，**也不回收已经发出去的**。

        收回去等于抢玩家手里的东西 —— 而且他可能已经合成掉了。
        """
        give, targets, _w = grants(self.milestone(threshold=1000),
                                   total={"pvp": {"kills": 250}},
                                   granted={"60001": 2})
        self.assertEqual({}, give)
        self.assertEqual({60001: 0}, targets)

    def test_lowering_the_threshold_settles_the_backlog_next_game(self):
        """运营把 100 调成 25：欠的账在**下一局结算**时一次还清，
        不用扫全表、不用离线任务。"""
        give, _t, _w = grants(self.milestone(threshold=25),
                              total={"pvp": {"kills": 250}},
                              granted={"60001": 2})
        self.assertEqual({60001: 8}, give)

    def test_the_lifetime_limit_caps_the_milestone(self):
        give, targets, _w = grants(self.milestone(limit=3),
                                   total={"pvp": {"kills": 9999}},
                                   granted={})
        self.assertEqual({60001: 3}, give)
        self.assertEqual({60001: 3}, targets)

    def test_a_rule_without_a_mode_adds_both_modes_up(self):
        only = [rule(60001, scope=shopcfg.CARD_SCOPE_TOTAL, metric="kills",
                     threshold=10)]
        give, _t, _w = grants(only, total={"pvp": {"kills": 6},
                                           "quest": {"kills": 5}})
        self.assertEqual({60001: 1}, give)


class MatchStatsTests(unittest.TestCase):
    """`RoomQuest` → 本局战绩。★ 用真的 `RoomQuest`，不是一个手搓的替身。"""

    def quest(self):
        import gameserver
        return gameserver.RoomQuest()

    def test_a_clean_sweep_sets_both_flags(self):
        quest = self.quest()
        stats = cards.match_stats(quest, 0, won=True, quest_mode=False,
                                  score=0)
        self.assertEqual(1, stats["win"])
        self.assertEqual(1, stats["perfect_win"])
        self.assertEqual(1, stats["carried"], "一个都没杀却赢了")
        self.assertEqual(1, stats["games"])

    def test_a_death_clears_the_perfect_flag(self):
        quest = self.quest()
        quest.deaths[0] = 1
        stats = cards.match_stats(quest, 0, won=True, quest_mode=False,
                                  score=0)
        self.assertEqual(0, stats.get("perfect_win", 0))

    def test_a_kill_clears_the_carried_flag(self):
        quest = self.quest()
        quest.enemy_kills[0] = 1
        stats = cards.match_stats(quest, 0, won=True, quest_mode=False,
                                  score=1)
        self.assertEqual(0, stats.get("carried", 0))

    def test_losing_clears_all_three(self):
        quest = self.quest()
        stats = cards.match_stats(quest, 0, won=False, quest_mode=False,
                                  score=0)
        for key in ("win", "perfect_win", "carried"):
            self.assertEqual(0, stats.get(key, 0), key)

    def test_the_weapon_counters_fold_into_keys(self):
        quest = self.quest()
        quest.weapon_stat(0, 110002)["kills"] = 4
        quest.weapon_stat(0, 110002)["shots"] = 30
        quest.weapon_stat(1, 110002)["kills"] = 9      # 别人的，不许串
        stats = cards.match_stats(quest, 0, won=False, quest_mode=False,
                                  score=0)
        self.assertEqual(4, stats["weapon_kills@110002"])
        self.assertEqual(30, stats["shots@110002"])

    def test_zero_values_are_dropped(self):
        """★ 只留非零项 —— `accounts.json` 不为「运营以后可能用的指标」变胖。"""
        quest = self.quest()
        stats = cards.match_stats(quest, 0, won=False, quest_mode=False,
                                  score=0)
        self.assertNotIn("kills", stats)
        self.assertNotIn("deaths", stats)
        self.assertEqual(1, stats["games"], "场次恒为 1，这一格必须在")


class DefaultRulesTests(unittest.TestCase):
    """出厂那 17 条规则本身要站得住。"""

    def setUp(self):
        self.rules = shopcfg.validate_cards(shopdefaults.default_cards())

    def test_every_card_has_exactly_one_rule(self):
        cards_seen = [r["card"] for r in self.rules]
        self.assertEqual(len(cards_seen), len(set(cards_seen)))
        self.assertEqual(set(shopcfg.ALL_CARDS), set(cards_seen))

    def test_every_default_rule_is_reachable(self):
        """★ 指标只在一种模式下有意义时，规则的模式必须是那一种 ——
        否则那条规则配得出来却**永远命中不了**（D17a 说的正是这种档）。"""
        for entry in self.rules:
            limited = shopcfg.card_metric_modes(entry["metric"])
            if limited:
                self.assertIn(entry.get("mode"), limited, entry["card"])

    def test_the_lucky_card_is_what_the_user_asked_for(self):
        """用户 2026-09-13 指定：对战每局格挡 ≥ 30 次**且获胜**才给一张。"""
        lucky = [r for r in self.rules if r["card"] == 60004][0]
        self.assertEqual("pvp", lucky["mode"])
        self.assertEqual(shopcfg.CARD_SCOPE_MATCH, lucky["scope"])
        self.assertEqual("guards", lucky["metric"])
        self.assertEqual(30, lucky["threshold"])
        self.assertTrue(lucky["win_only"])
        self.assertEqual(1, lucky["count"])

    def test_each_weapon_card_counts_its_own_weapon(self):
        for roh in shopcfg.WEAPON_CARDS:
            entry = [r for r in self.rules if r["card"] == roh][0]
            self.assertEqual("weapon_kills", entry["metric"])
            self.assertEqual(roh, entry["weapon"], roh)

    def test_every_rule_reads_as_a_sentence(self):
        """念出来的那句话是三处共用的（管理页浮窗 / 游戏提示框 / 冲突提示）。

        ⚠ 里面**绝不能出现裸 `|`** —— 客户端拿它当说明的分段符
        （`wcstok`，`0x5fa904`），混进去会把一段说明拦腰切开。
        """
        for entry in self.rules:
            line = shopcfg.describe_card_rule(entry, shopcfg.item_name(entry["card"]))
            self.assertTrue(line, entry["card"])
            self.assertNotIn(shopcfg.DESC_SEPARATOR, line, entry["card"])
            self.assertNotIn("None", line, entry["card"])
            self.assertNotIn(entry["metric"], line,
                             "指标的英文 key 漏进人话里了")

    def test_a_switched_off_rule_says_so(self):
        line = shopcfg.describe_card_rule({"card": 60001, "listed": False})
        self.assertEqual("暂时无法获得", line)


class OneShotUpgradeTests(unittest.TestCase):
    """★★ 「给老服务器补 17 条称号配方」是**一次性升级**（用户 2026-09-13）。

    判据是一个**事件**：这一次启动刚刚新建了 `cards.json`（铁律 10）——
    不是标记文件、不是计数器。三条路各验一遍，第三条最要紧：
    **运营后来删掉的配方不许自己回来**（铁律 11）。
    """

    def setUp(self):
        import tempfile
        # ⚠ **不许 import `app`**：那个模块 import 时就 `asynclog.start()`，
        #   把日志切成异步写 —— `test_logs` 里那几条断言 stdout 的用例会集体翻
        #   （踩过一次）。升级的**判断和写盘**因此住在 `shopcfg` 里，
        #   `app` 那边只剩说话。
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        saved = shopcfg.DATA_DIR
        shopcfg.DATA_DIR = self.tmp.name
        self.addCleanup(shopcfg.invalidate)
        self.addCleanup(setattr, shopcfg, "DATA_DIR", saved)
    def recipes_on_disk(self):
        import json
        with open(shopcfg.path_of(shopcfg.RECIPE_FILENAME, self.tmp.name),
                  encoding="utf-8") as fp:
            return json.load(fp)["recipes"]

    def write_recipes(self, rows):
        shopcfg.write_json(
            shopcfg.path_of(shopcfg.RECIPE_FILENAME, self.tmp.name),
            {"format": shopcfg.FORMAT, "recipes": rows})
        shopcfg.invalidate(self.tmp.name)

    def titles_in(self, rows):
        import shopdata
        return {r["result"] for r in rows
                if shopdata.kind(r["result"]) == "title"}

    def test_a_brand_new_server_needs_no_upgrade(self):
        """全新装的：两份配置同时生成 ⇒ `recipe.json` 本来就是全的，空跑。"""
        created = shopcfg.ensure_files(self.tmp.name)
        self.assertIn(shopcfg.CARDS_FILENAME, created)
        before = self.recipes_on_disk()
        added = shopcfg.apply_first_run_upgrades(created, self.tmp.name)
        self.assertEqual(before, self.recipes_on_disk())
        self.assertEqual({}, added)

    def test_an_old_server_gets_the_title_recipes_once(self):
        """老服务器：只有 `cards.json` 是新建的 ⇒ 把缺的 17 条补上。"""
        # 先铺一份「这一版之前」的 recipe.json：把称号那几条去掉。
        full = shopcfg.validate_recipes(shopcfg.default_recipes())
        old = [r for r in full if r["result"] not in self.titles_in(full)]
        self.assertTrue(self.titles_in(full), "默认配方里居然没有称号")
        self.write_recipes(old)
        # 顺手改一条，验「只增不改」。
        old[0]["cost"] = 424242
        self.write_recipes(old)

        created = shopcfg.ensure_files(self.tmp.name)
        self.assertIn(shopcfg.CARDS_FILENAME, created)
        self.assertNotIn(shopcfg.RECIPE_FILENAME, created)
        added = shopcfg.apply_first_run_upgrades(created, self.tmp.name)

        now = self.recipes_on_disk()
        self.assertEqual(self.titles_in(full), self.titles_in(now))
        self.assertEqual(len(old) + len(self.titles_in(full)), len(now))
        kept = [r for r in now if r["result"] == old[0]["result"]][0]
        self.assertEqual(424242, kept["cost"], "运营改过的值被默认值盖掉了")
        self.assertEqual(len(self.titles_in(full)),
                         len(added[shopcfg.RECIPE_FILENAME]))

    def test_the_second_start_never_brings_deleted_recipes_back(self):
        """★★ 这一条是整个「一次性」的意义所在（铁律 11）。

        第二次启动 `cards.json` 已经在了 ⇒ 整个跳过 ⇒ 运营删掉的配方
        **不会自己回来**。`backfill_defaults()` 是幂等的，但它不是「一次性」
        —— 每次启动都叫它的话，删一条回来一条，而且没人看得出为什么。
        """
        created = shopcfg.ensure_files(self.tmp.name)
        added = shopcfg.apply_first_run_upgrades(created, self.tmp.name)
        # 运营在管理页上删掉了一条配方。
        rows = [r for r in self.recipes_on_disk()][:-1]
        self.write_recipes(rows)
        # 第二次启动：`ensure_files` 什么都没建。
        created2 = shopcfg.ensure_files(self.tmp.name)
        self.assertEqual([], created2)
        shopcfg.apply_first_run_upgrades(created2, self.tmp.name)
        self.assertEqual(len(rows), len(self.recipes_on_disk()),
                         "删掉的配方自己回来了")


class StaleNameRefreshTests(unittest.TestCase):
    """★★ 「出厂名改过」的那几件 —— 判据是**盘上那个名字还等于我们当初发出去的**。

    这不是「拿默认值盖掉用户的设置」，是「这一格他从来没碰过，而我们换了
    出厂值，替他带过去」。**运营自己改过的名字一个都不碰**（铁律 11）。
    """

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        saved = shopcfg.DATA_DIR
        shopcfg.DATA_DIR = self.tmp.name
        self.addCleanup(shopcfg.invalidate)
        self.addCleanup(setattr, shopcfg, "DATA_DIR", saved)

    def write_items(self, names):
        """铺一份 `items.json`：`names` 是 `{id: 名字}` 的覆盖。"""
        doc = shopcfg.default_items()
        for entry in doc["items"]:
            if entry["id"] in names:
                entry["name"] = names[entry["id"]]
        shopcfg.write_json(
            shopcfg.path_of(shopcfg.ITEMS_FILENAME, self.tmp.name), doc)
        shopcfg.invalidate(self.tmp.name)

    def names_on_disk(self):
        import json
        with open(shopcfg.path_of(shopcfg.ITEMS_FILENAME, self.tmp.name),
                  encoding="utf-8") as fp:
            return {e["id"]: e["name"] for e in json.load(fp)["items"]}

    def test_the_table_actually_describes_a_rename(self):
        """表里每一条都得真的换过名字 —— 不然那一条是死的，只会让人以为它管用。"""
        import shopdata
        import shopdefaults
        self.assertTrue(shopcfg.RENAMED_DEFAULT_NAMES)
        for item_id, was in shopcfg.RENAMED_DEFAULT_NAMES.items():
            item = shopdata.get(item_id)
            self.assertIsNotNone(item, item_id)
            self.assertNotEqual(was, shopdefaults.name_of(item), item_id)

    def test_an_untouched_old_name_gets_carried_forward(self):
        self.write_items(dict(shopcfg.RENAMED_DEFAULT_NAMES))
        changed = shopcfg.refresh_stale_names(self.tmp.name, apply=True)
        self.assertEqual(sorted(shopcfg.RENAMED_DEFAULT_NAMES),
                         sorted(i for i, _w, _n in changed))
        names = self.names_on_disk()
        for item_id, was in shopcfg.RENAMED_DEFAULT_NAMES.items():
            self.assertNotEqual(was, names[item_id], item_id)

    def test_a_name_the_operator_changed_is_never_touched(self):
        """★★ 这一条是整条判据的意义所在（铁律 11）。"""
        mine = dict((i, "我自己起的名字%d" % i)
                    for i in shopcfg.RENAMED_DEFAULT_NAMES)
        self.write_items(mine)
        self.assertEqual([], shopcfg.refresh_stale_names(self.tmp.name,
                                                         apply=True))
        names = self.names_on_disk()
        for item_id, wanted in mine.items():
            self.assertEqual(wanted, names[item_id], item_id)

    def test_it_is_idempotent(self):
        """★ 判据自带幂等：刷完盘上的名字已经不等于旧名了，再跑就是空的
        —— 所以它不需要「跑过没有」的标记。"""
        self.write_items(dict(shopcfg.RENAMED_DEFAULT_NAMES))
        self.assertTrue(shopcfg.refresh_stale_names(self.tmp.name, apply=True))
        self.assertEqual([], shopcfg.refresh_stale_names(self.tmp.name,
                                                         apply=True))

    def test_a_dry_run_writes_nothing(self):
        self.write_items(dict(shopcfg.RENAMED_DEFAULT_NAMES))
        before = self.names_on_disk()
        self.assertTrue(shopcfg.refresh_stale_names(self.tmp.name))
        self.assertEqual(before, self.names_on_disk())

    def test_nothing_else_in_the_file_moves(self):
        """只动名字那一格 —— 等级 / 角色限定 / 别的 807 件一个字都不许变。"""
        self.write_items(dict(shopcfg.RENAMED_DEFAULT_NAMES))
        before = self.names_on_disk()
        shopcfg.refresh_stale_names(self.tmp.name, apply=True)
        after = self.names_on_disk()
        self.assertEqual(len(before), len(after))
        moved = {i for i in before if before[i] != after[i]}
        self.assertEqual(set(shopcfg.RENAMED_DEFAULT_NAMES), moved)

    def test_a_missing_or_broken_file_is_left_alone(self):
        """读不懂就别动它（D10）—— 也不许抛。"""
        self.assertEqual([], shopcfg.refresh_stale_names(self.tmp.name,
                                                         apply=True))
        with open(shopcfg.path_of(shopcfg.ITEMS_FILENAME, self.tmp.name),
                  "w", encoding="utf-8") as fp:
            fp.write("{ 这不是 json")
        self.assertEqual([], shopcfg.refresh_stale_names(self.tmp.name,
                                                         apply=True))

    def test_the_first_run_upgrade_does_it_too(self):
        """开服那一次性升级里也带着它 —— 云上不用有人记得去跑命令。"""
        self.write_items(dict(shopcfg.RENAMED_DEFAULT_NAMES))
        created = shopcfg.ensure_files(self.tmp.name)
        self.assertIn(shopcfg.CARDS_FILENAME, created)
        shopcfg.apply_first_run_upgrades(created, self.tmp.name)
        names = self.names_on_disk()
        for item_id, was in shopcfg.RENAMED_DEFAULT_NAMES.items():
            self.assertNotEqual(was, names[item_id], item_id)


class CardTooltipTests(unittest.TestCase):
    """★ 卡片的物品提示框（用户 2026-09-13）——**游戏里和管理页同一个出处**。

    `shopcfg.item_desc_zh()` 的结果既进 `ItemInfo +0x18`（客户端提示框下半），
    又进管理页 catalog 的 `desc`（`paintTip` 照着画）。改一个函数两边同时亮。
    """

    def setUp(self):
        import shopdata
        import tempfile
        self.shopdata = shopdata
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        saved = shopcfg.DATA_DIR
        shopcfg.DATA_DIR = self.tmp.name
        self.addCleanup(shopcfg.invalidate)
        self.addCleanup(setattr, shopcfg, "DATA_DIR", saved)
        # ★ 全量测试把 `DATA_DIR` 指到一个**空目录**上（`run_tests.py` 的注释
        #   说了为什么），所以要验说明就得自己铺一份配置。
        shopcfg.ensure_files(self.tmp.name)
        shopcfg.invalidate(self.tmp.name)

    def desc(self, item_id):
        return shopcfg.item_desc_zh(self.shopdata.get(item_id))

    def test_every_card_says_how_to_get_it_and_what_it_makes(self):
        for card in shopcfg.ALL_CARDS:
            text = self.desc(card)
            self.assertIn("获得条件：", text, card)
            self.assertIn(shopcfg.CARD_CRAFT_PREFIX, text, card)  # 能合成什么
            self.assertIn("金币", text, card)          # 要花多少

    def test_the_title_effect_comes_along(self):
        """★ 用户 2026-09-13 追加：**关联显示对应称号的加成效果**。

        效果那句话和「穿上身之后提示框里写的」**必须是同一句**
        —— 两处都走 `_bonus_lines` + `_effect_lines`。
        """
        text = self.desc(60004)
        self.assertIn(shopcfg.title_effect_zh(560004), text)
        self.assertIn("50%", text, "[幸运幸存者] 那句 exe 硬编码的效果没带上")
        # 武器称号的条件加成（Lua）也要带上。
        self.assertIn(shopcfg.title_effect_zh(610001), self.desc(110001))

    def test_a_card_with_no_recipe_says_nothing_about_crafting(self):
        """★ 没有配方就不写那一段（用户 2026-09-13）。"""
        rows = [r for r in shopcfg.validate_recipes(shopcfg.default_recipes())
                if r["result"] != 610001]
        self.write_recipes(rows)
        text = self.desc(110001)
        self.assertIn("获得条件：", text)
        self.assertNotIn(shopcfg.CARD_CRAFT_PREFIX, text)
        self.assertNotIn(shopcfg.DESC_SEPARATOR, text, "空段也不该切出来")

    def test_a_recipe_that_needs_several_cards_lists_them_all(self):
        """★ 一个称号要好几种卡片时**每一种都要写出来**（用户 2026-09-13）。

        只写「500 张」会让人以为攒够这一种就合得出来。
        ★ 本卡片排最前面 —— 提示框是停在它身上弹出来的。
        """
        rows = shopcfg.validate_recipes(shopcfg.default_recipes())
        for entry in rows:
            if entry["result"] == 560004:
                entry["materials"] = [{"id": 60004, "count": 500},
                                      {"id": 60006, "count": 50}]
        self.write_recipes(rows)
        line = [l for l in self.desc(60004).split("\n")
                if l.startswith(shopcfg.CARD_CRAFT_PREFIX)][0]
        self.assertIn("幸运卡片×500", line)
        self.assertIn("红心卡片×50", line)
        self.assertLess(line.index("幸运卡片"), line.index("红心卡片"))
        # 反过来停在红心卡片上时，红心排最前面。
        other = [l for l in self.desc(60006).split("\n")
                 if l.startswith(shopcfg.CARD_CRAFT_PREFIX)
                 and "幸运幸存者" in l][0]
        self.assertLess(other.index("红心卡片"), other.index("幸运卡片"))

    def test_a_card_used_by_several_titles_lists_them_all(self):
        """★ 一张卡片能合成好几个称号时，一个一行（用户 2026-09-13）。"""
        rows = shopcfg.validate_recipes(shopcfg.default_recipes())
        for entry in rows:
            if entry["result"] in (560005, 560006):
                entry["materials"] = [{"id": 60004, "count": 20}]
        self.write_recipes(rows)
        text = self.desc(60004)
        for title in ("[幸运幸存者]", "[手下留情]", "[红心达人]"):
            self.assertIn(title, text, title)
        # 三个称号 = 1 行条件 + 3 行，还在第 1 段 5 行的预算里 ⇒ 不该截断。
        self.assertNotIn("另有", text)
        segments = text.split(shopcfg.DESC_SEPARATOR)
        self.assertLessEqual(len(segments[0].split("\n")),
                             shopcfg.ITEM_DESC_MAX_LINES)
        self.assertLessEqual(len(segments[1].split("\n")),
                             shopcfg.ITEM_DESC_MAX_LINES_2)

    def test_every_title_that_uses_the_card_shows_its_own_effect(self):
        """★ **几个称号就写几条效果，每条行首是称号名**（用户 2026-09-13）。

        用户的实际配置就是这一型：`[队内间谍]` 要两种卡片，而其中一种
        `完美卡片` 同时又是 `[完美胜利者]` 的材料 —— 停在那张卡上时
        **两个称号的效果都得写出来**，只写一条人分不清写的是哪个。
        """
        rows = shopcfg.validate_recipes(shopcfg.default_recipes())
        for entry in rows:
            if entry["result"] == 560007:
                entry["materials"] = [{"id": 60007, "count": 20},
                                      {"id": 60001, "count": 1}]
        self.write_recipes(rows)
        effects = self.desc(60001).split(shopcfg.DESC_SEPARATOR)[1]
        lines = effects.split("\n")
        self.assertEqual(2, len(lines), effects)
        for title in ("[完美胜利者]", "[队内间谍]"):
            line = [l for l in lines if l.startswith(title)]
            self.assertEqual(1, len(line), title)
            self.assertIn(shopcfg.title_effect_zh(
                560001 if title == "[完美胜利者]" else 560007), line[0])

    def test_the_effect_line_carries_the_title_name_even_when_alone(self):
        """★ 只有一个称号时**也**写名字（用户 2026-09-13）。

        不写的话那一行看上去像是**卡片自己**的效果 —— 卡片穿不上身，
        它没有效果。
        """
        effects = self.desc(60004).split(shopcfg.DESC_SEPARATOR)[1]
        self.assertTrue(effects.startswith("[幸运幸存者]"), effects)

    def test_too_many_titles_get_truncated_but_still_say_so(self):
        """放不下的那些至少要说一声有 —— 不说的话玩家会以为只有这几个。"""
        rows = shopcfg.validate_recipes(shopcfg.default_recipes())
        for entry in rows:
            if entry["result"] in (560001, 560002, 560003, 560005, 560006,
                                   560007, 560008):
                entry["materials"] = [{"id": 60004, "count": 20}]
        self.write_recipes(rows)
        text = self.desc(60004)
        self.assertIn("另有", text)
        segments = text.split(shopcfg.DESC_SEPARATOR)
        self.assertLessEqual(len(segments[0].split("\n")),
                             shopcfg.ITEM_DESC_MAX_LINES)
        self.assertLessEqual(len(segments[1].split("\n")),
                             shopcfg.ITEM_DESC_MAX_LINES_2)

    def write_recipes(self, rows):
        shopcfg.write_json(
            shopcfg.path_of(shopcfg.RECIPE_FILENAME, self.tmp.name),
            {"format": shopcfg.FORMAT, "recipes": rows})
        shopcfg.invalidate(self.tmp.name)

    def test_the_description_never_contains_a_bare_separator_in_the_text(self):
        """⚠ `|` 是客户端的**分段符**（`wcstok`，`0x5fa904`）。

        说明正文里混进一个就会把一段拦腰切开，而且**客户端不会报错** ——
        画面上只会少半句话。所以段数必须 ≤2（一个分隔符）。
        """
        for card in shopcfg.ALL_CARDS:
            self.assertLessEqual(
                self.desc(card).count(shopcfg.DESC_SEPARATOR), 1, card)

    def test_each_segment_fits_the_client_box(self):
        """客户端**只画前 2 段**，第 1 段 ≈5 行、第 2 段 ≈3 行（§31 / §53）。"""
        for card in shopcfg.ALL_CARDS:
            segments = self.desc(card).split(shopcfg.DESC_SEPARATOR)
            self.assertLessEqual(len(segments), 2, card)
            self.assertLessEqual(len(segments[0].split("\n")),
                                 shopcfg.ITEM_DESC_MAX_LINES, card)
            if len(segments) > 1:
                self.assertLessEqual(len(segments[1].split("\n")),
                                     shopcfg.ITEM_DESC_MAX_LINES_2, card)

    def test_an_ordinary_material_still_has_no_description(self):
        """★ 只有卡片是例外：珠子 / 矿料照旧留白（`KIND_USAGE_ZH` 故意不收）。"""
        self.assertEqual("", self.desc(10001))       # 黑色小珠
        self.assertEqual("", self.desc(30018))       # 青铜管

    def test_a_card_that_cannot_be_earned_says_so(self):
        rules = shopcfg.validate_cards(shopdefaults.default_cards())
        for entry in rules:
            entry["listed"] = False
        shopcfg.write_json(
            shopcfg.path_of(shopcfg.CARDS_FILENAME, self.tmp.name),
            {"format": shopcfg.FORMAT, "rules": rules})
        shopcfg.invalidate(self.tmp.name)
        self.assertIn("暂时无法获得", self.desc(60004))

    def test_changing_the_rule_changes_the_description(self):
        """★ 改完保存即刻生效（服务端这一侧）—— 说明是**现算**的，不是快照。"""
        self.assertIn("达到 30", self.desc(60004))
        rules = shopcfg.validate_cards(shopdefaults.default_cards())
        for entry in rules:
            if entry["card"] == 60004:
                entry["threshold"] = 7
        shopcfg.write_json(
            shopcfg.path_of(shopcfg.CARDS_FILENAME, self.tmp.name),
            {"format": shopcfg.FORMAT, "rules": rules})
        shopcfg.invalidate(self.tmp.name)
        self.assertIn("达到 7", self.desc(60004))

    def test_the_fight_master_title_warns_that_it_never_fires(self):
        """⚠ `560002` 的加成要求格斗模式，而中国区客户端根本选不到那个模式
        —— 说明里必须写明白，不然玩家攒 30 张卡换一个空壳。"""
        self.assertIn("不会触发", self.desc(560002))


if __name__ == "__main__":
    unittest.main()
