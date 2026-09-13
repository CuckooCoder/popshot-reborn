#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""称号卡片 —— 打完一局该发谁几张卡（用户 2026-09-13，V0.3商店）。

原版的卡片是**按成就**给的（击杀数 / 误伤次数…，FINDINGS §37），所以
V0.3 第一稿把「按概率掉卡片」整片砍掉了（D44a）——「本项目还没有成就统计，
随机掉等于把荣誉卡片变成抽奖」。这一版补上的就是那个缺的前提。

## 三段式，每一段都只做一件事

    RoomQuest（gameserver）   一局里发生了什么   ← 数出来
    cards.py（本模块）        该发谁几张卡        ← 纯函数，好写单测
    account_store            落盘                ← 一把锁一次写盘

本模块**不碰网络、不读盘、不写盘**：规则表由调用方从 `shopcfg.cards()` 读好
传进来（结算时读一次），统计值由调用方从 `RoomQuest` 取好传进来。

## 两种统计范围

* **一局内**（`match`）：这一局打到这个数就给。一局一次机会，
  `quest.settled` 保证一局只结算一次 ⇒ 天然去重。
* **玩家累计**（`total`）：**里程碑，可重复**（用户 2026-09-13 拍板）——
  每满一个阈值给一次。

      应发总数 = min(上限, 累计值 // 阈值) × 每次给几张
      本局发   = max(0, 应发总数 − 这个账号已经从这条规则拿过几张)

  这三行同时满足三件事：**幂等**（同一份统计再算一遍发 0 张）、
  **运营调高阈值不倒扣**（已经发出去的留在玩家仓库里，收回去等于抢东西）、
  **调低阈值下一局一次补齐欠账**（不用扫全表、不用离线任务）。

## ★ 只有「达到」一种比较，没有「不超过」

「零死亡获胜」「零击杀获胜」这类条件靠 **0 / 1 的旗标指标**表达
（`perfect_win` / `carried`，见 `shopcfg.CARD_FLAG_METRICS`）。
这样既少一个下拉，也躲开了「阈值填 0 时『不超过』恒成立、那条规则每局
白送一张而且没人看得出为什么」那个很难查的坑 ——
对应地 `validate_cards` 把阈值下限卡在 1。

## 铁律：只用标准库

CPython 3.8（Win7 运行时）也要能跑。
"""
from __future__ import annotations

import shopcfg

#: 武器维度折进统计键里的分隔符：`"kills@110001"`。
#: ★ 折进键而不是多一层字典 —— 多一层会让洗表 / 累加 / 读取三处各多一段分支
#:   （`account_store.battle_stats` 的注释里写着同一条）。
WEAPON_SEP = "@"


def stat_key(metric, weapon=None):
    """`("kills", None)` → `"kills"`；`("kills", 110001)` → `"kills@110001"`。"""
    if weapon is None:
        return str(metric)
    return "%s%s%d" % (metric, WEAPON_SEP, int(weapon))


def stat_of(stats, mode, metric, weapon=None):
    """从累计战绩里取一个数。`mode` 给 `None` 时**两种模式相加**（= 规则不限模式）。

    ★ 命中率是**现算**的（`hits × 100 ÷ shots`）—— 存比率的话两局
    50% 和 100% 加起来会变成 150%（`account_store` 那边也写着这一条）。
    """
    if metric == "accuracy":
        shots = stat_of(stats, mode, "shots", weapon)
        if shots <= 0:
            return 0
        return stat_of(stats, mode, "hits", weapon) * 100 // shots
    key = stat_key(metric, weapon)
    if mode is not None:
        bucket = (stats or {}).get(mode)
        return int((bucket or {}).get(key, 0))
    total = 0
    for name in shopcfg.CARD_MODES:
        bucket = (stats or {}).get(name)
        total += int((bucket or {}).get(key, 0))
    return total


def merge_stats(before, mode, gained):
    """累计战绩 + 本局增量 → 新的累计战绩。**不改传进来的那份**。

    ★ **只留非零项**：零值不写进去，历史上留下的零值也顺手丢掉
    （`accounts.json` 不为「运营以后可能会用的指标」白白撑大一圈）。
    """
    out = {}
    for name, bucket in (before or {}).items():
        clean = dict((key, int(value)) for key, value in (bucket or {}).items()
                     if int(value) > 0)
        if clean:
            out[name] = clean
    bucket = dict(out.get(mode, {}))
    for key, value in (gained or {}).items():
        value = int(value)
        if value <= 0:
            continue
        bucket[key] = bucket.get(key, 0) + value
    if bucket:
        out[mode] = dict(sorted(bucket.items()))
    return out


def match_stats(quest, seat, *, won, quest_mode, score):
    """一局打完，这个座位的战绩 `{统计键: 值}`（含武器维度的 `指标@族号`）。

    `quest` 是 `gameserver.RoomQuest`。**只读，不改它**。

    ★ 旗标指标（`win` / `perfect_win` / `carried` / `games`）在这儿算成 0 / 1
    —— 它们既是「一局内」的条件，累计起来又正好是「赢了几场 / 打了几场」，
    一个口径两用。
    """
    deaths = _cell(quest.deaths, seat)
    kills = _cell(quest.enemy_kills, seat) + _cell(quest.mob_kills, seat)
    hits = _cell(quest.hits, seat)
    shots = _cell(quest.shots, seat)
    out = {
        "kills": _cell(quest.enemy_kills, seat),
        "mob_kills": _cell(quest.mob_kills, seat),
        "team_kills": _cell(quest.team_kills, seat),
        "suicides": _cell(quest.suicides, seat),
        "deaths": deaths,
        "shots": shots,
        "hits": hits,
        "splash_hits": _cell(quest.splash_hits, seat),
        "damage": _cell(quest.damage_out, seat),
        "crits": _cell(quest.crits, seat),
        "guards": _cell(quest.guards, seat),
        "dashes": _cell(quest.dashes, seat),
        "hearts": _cell(quest.hearts, seat),
        "coins": _cell(quest.coins, seat),
        "score": max(0, int(score or 0)),
        "games": 1,
        "win": 1 if won else 0,
        # 「完美胜利」= 赢了而且一次都没死；「零击杀获胜」= 赢了但一个都没杀。
        # ★ 闯关里 `kills` 恒 0（怪走 `mob_kills`），所以 `carried` 在闯关下
        #   会对任何一次通关都成立 —— `CARD_METRICS` 因此把它限定成只有对战
        #   才有意义，validator 也照那张表拦着。
        "perfect_win": 1 if (won and deaths == 0) else 0,
        "carried": 1 if (won and kills == 0) else 0,
    }
    for (stat_seat, roh), cell in (quest.weapon_stats or {}).items():
        if stat_seat != seat:
            continue
        for name, metric in (("shots", "shots"), ("kills", "weapon_kills"),
                             ("damage", "weapon_damage")):
            value = int(cell.get(name, 0))
            if value > 0:
                out[stat_key(metric, roh)] = value
        # ★ 「某武器命中数」没有单独的计数器（`rpExplode` 里没有武器 id，
        #   归账靠的是「最近一发开的什么枪」）—— 所以按武器的命中率
        #   拿不到，`CARD_METRICS` 里也就没有那一项。
    return dict((key, value) for key, value in out.items() if value)


def _cell(row, seat):
    try:
        return int(row[seat])
    except (IndexError, TypeError, ValueError):
        return 0


def due_grants(rules, *, mode, stage, difficulty, won, match, total, granted):
    """该发这个玩家几张卡。返回 `({卡片: 张数}, {卡片: 新的累计目标}, [警告])`。

    * `rules`   —— `shopcfg.cards()` 读出来的那一份
    * `mode`    —— 这一局是 `"pvp"` 还是 `"quest"`
    * `match`   —— `match_stats()` 算出来的本局战绩
    * `total`   —— 这个账号**加上本局之后**的累计战绩
    * `granted` —— `{卡片 id 字符串: 已经从这条规则拿过几张}`

    第二个返回值是「累计档的新目标」，调用方把它交给
    `account_store.apply_battle()`，由存档层在**同一把锁里**算
    `max(0, 目标 − 已发)` 并落盘 —— 这样存储层完全不认识规则，也不用回调。
    """
    give = {}
    targets = {}
    warnings = []
    for rule in rules or ():
        if not rule.get("listed"):
            continue
        card = rule.get("card")
        metric = rule.get("metric")
        if card is None or metric not in shopcfg.CARD_METRICS:
            warnings.append("规则里有认不出的指标 %r，已跳过" % (metric,))
            continue
        if not _applies(rule, mode=mode, stage=stage, difficulty=difficulty,
                        won=won, match=match):
            continue
        threshold = max(1, int(rule.get("threshold", 1)))
        count = max(1, int(rule.get("count", 1)))
        limit = max(0, int(rule.get("limit", 0)))
        weapon = rule.get("weapon")
        if not shopcfg.card_metric_needs_weapon(metric):
            weapon = None
        scope = rule.get("scope", shopcfg.CARD_SCOPE_MATCH)
        done = int((granted or {}).get(str(card), 0))
        if scope == shopcfg.CARD_SCOPE_TOTAL:
            # 里程碑：`应发总数 − 已发`。★ 目标算在这儿、减法留给存档层
            #   （见 docstring）—— 存档层不认识规则，也就不会有回调和重入。
            times = stat_of(total, rule.get("mode"), metric, weapon) // threshold
            if limit:
                times = min(times, limit)
            target = times * count
            targets[card] = target
            if target > done:
                give[card] = give.get(card, 0) + (target - done)
            continue
        # 一局内：达标就给。`limit` 是这个账号一辈子的上限。
        value = _match_value(match, metric, weapon)
        if value < threshold:
            continue
        if limit and done >= limit * count:
            continue
        amount = count
        if limit:
            amount = min(amount, limit * count - done)
        if amount > 0:
            give[card] = give.get(card, 0) + amount
    return give, targets, warnings


def _match_value(match, metric, weapon):
    """本局战绩里那一个数。命中率现算（和累计那一侧同一个口径）。"""
    if metric == "accuracy":
        shots = int((match or {}).get(stat_key("shots", weapon), 0))
        if shots <= 0:
            return 0
        return int((match or {}).get(stat_key("hits", weapon), 0)) * 100 // shots
    return int((match or {}).get(stat_key(metric, weapon), 0))


def _applies(rule, *, mode, stage, difficulty, won, match):
    """这条规则管不管这一局。"""
    want = rule.get("mode")
    if want is not None and want != mode:
        return False
    if rule.get("stage") is not None and rule["stage"] != stage:
        return False
    if rule.get("difficulty") is not None and rule["difficulty"] != difficulty:
        return False
    if rule.get("win_only") and not won:
        return False
    floor = int(rule.get("min_shots", 0) or 0)
    if floor:
        # ★ 样本下限看的是**本局**开枪数，累计档也一样 —— 「一局只开三枪
        #   的人不该靠命中率拿卡」说的就是这一局，不是他这辈子。
        weapon = rule.get("weapon")
        if not shopcfg.card_metric_needs_weapon(rule.get("metric")):
            weapon = None
        if int((match or {}).get(stat_key("shots", weapon), 0)) < floor:
            return False
    return True
