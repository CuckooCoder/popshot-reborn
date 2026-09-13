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

## ★★ 一条规则 = 对局模式 + 一串条件（用户 2026-09-13 第三轮）

管理页那一行右边两颗钮就是这两段：

    对局模式   mode / stage / difficulty   ← `_applies`，这一局算不算数
    达成条件   conditions（and / or 串起来）← `eval_conditions`

**达成一次固定发一张**（`shopcfg.CARD_GRANT_COUNT`）—— 没有「数量」
也没有「累计上限」两格，页面上只看那句现算的说明文。

## 两种统计范围（**每条条件各有一个**）

* **一局内**（`match`）：这一局打到这个数。一局一次机会，
  `quest.settled` 保证一局只结算一次 ⇒ 天然去重。
* **玩家累计**（`total`）：**从上一次拿到这张卡之后重新攒**（用户原话：
  「发过奖励后，两个计数器同时归零，重新开始新一轮计数」）。

      这一轮攒了多少 = 现在的累计值 − 上次归零时的累计值（存档里的 `card_bases`）

  所以它念成「每满 N」而不是「累计达到 N」。★★ 正因为会归零，
  它才拼得进 and / or：「累计击杀每满 100 **并且** 累计对局每满 10」
  问的是「两个计数器是不是都攒够了」，攒够就发一张、两个一起归零。
  归零之后进度都是 0 ⇒ **一次结算最多发一张**，运营把阈值调小也不会
  瞬间刷出一堆（不需要循环，也不需要一个「上限」来兜底）。

## ★★ 条件 = 统计范围 + 指标 + 比较符 + 数值

指标表里**只留原子计数**，条件靠比较符拼：
「死亡次数 · 等于 · 0」并且「本局结果 · 为 · 胜利 / 通关」就是完美胜利。

当初躲着不做 `≤` 是怕「阈值填 0 时恒成立、每局白送一张、页面上还看着正常」。
那个坑用**结构性护栏**堵（都在 `shopcfg._validate_card_conditions` 里）：

    ge 大于等于  阈值下限 1     ←「≥ 0」恒成立，配不出来
    le / eq      阈值下限 0     ←「一次都没死」要的正是 0
    累计条件只能 ge（念「每满 N」）← 刚归零那一刻别的方向对谁都成立
    le / eq 不许带武器维度      ← 取不到的键是 0，见 `_match_value`
    命中率必须配一条「开枪次数 ≥ N」，且整条规则不许出现「或者」

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
    """`("kills", None)` → `"kills"`；`("kills", 110001)` → `"kills@110001"`。

    ★ 指标名和统计键**不一定同名**（`shopcfg.card_metric_stat`）：
    「本局结果」和「胜利 / 通关次数」读的是同一格 `win`，所以存档里
    不会因为多一个指标就多一个计数器。
    """
    name = shopcfg.card_metric_stat(metric)
    if weapon is None:
        return str(name)
    return "%s%s%d" % (name, WEAPON_SEP, int(weapon))


def card_op_holds(op, value, threshold):
    """这个数满不满足条件。**全项目唯一的比较落点**。

    ★ 认不出的比较符按「大于等于」算 —— 规则表里 `op` 缺省就是它。
    """
    if op == shopcfg.CARD_OP_LE:
        return value <= threshold
    if op == shopcfg.CARD_OP_EQ:
        return value == threshold
    return value >= threshold


def stat_of(stats, mode, metric, weapon=None):
    """从累计战绩里取一个数。`mode` 给 `None` 时**两种模式相加**（= 规则不限模式）。

    ★★ **比率是现算的，绝不许存**（全表只有「命中率」一条）：存进去的话
    两局 50% 和 100% 加起来会变成 150%。所以累计战绩里只有 `hits` / `shots`
    两个分量，比率在读的时候才除（`account_store` 那边写着同一条）。
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

    ★ `win` / `games` 在这儿算成 0 / 1。「一共赢了几场」是从每局这一笔
    累加出来的，不写就没有。★★ 一局内那个「本局结果」指标（`won`）读的
    **就是 `win` 这一格**（`shopcfg.CARD_METRIC_STAT`）—— 两个指标共一个
    计数器，存档里不会因此多一个键。

    ★★ **`kills` 是杀人 + 杀怪的和**（用户 2026-09-13 第二轮）：
    对战里怪恒 0、闯关里人恒 0，所以在**任何一个模式桶**里它都正好等于
    「这个模式下的击杀数」——「杀人还是杀怪」由规则里那个「模式」格子回答，
    不需要两个指标。
    ⚠ **别再单独写一份 `mob_kills`**：那一格已经加进 `kills` 了，
    两边都写以后谁给 `kills` 加一次读时求和就会重复计数。
    """
    deaths = _cell(quest.deaths, seat)
    out = {
        "kills": _cell(quest.enemy_kills, seat) + _cell(quest.mob_kills, seat),
        "team_kills": _cell(quest.team_kills, seat),
        "suicides": _cell(quest.suicides, seat),
        "deaths": deaths,
        "shots": _cell(quest.shots, seat),
        "hits": _cell(quest.hits, seat),
        "splash_hits": _cell(quest.splash_hits, seat),
        "damage": _cell(quest.damage_out, seat),
        "crits": _cell(quest.crits, seat),
        "guards": _cell(quest.guards, seat),
        # ★ 只数**命中**（`dash_hits`），不数发动：空房间连点就达标的成就
        #   没有意义（用户 2026-09-13）。`quest.dashes` 还在，只是不再当指标。
        "dash_hits": _cell(quest.dash_hits, seat),
        "hearts": _cell(quest.hearts, seat),
        "coins": _cell(quest.coins, seat),
        "score": max(0, int(score or 0)),
        "games": 1,
        "win": 1 if won else 0,
    }
    for (stat_seat, roh), cell in (quest.weapon_stats or {}).items():
        if stat_seat != seat:
            continue
        # ★ 武器维度的键名**和主指标同名**（`kills@110001`）—— 武器只是
        #   一个筛选维度，不是另一个指标。`note_weapon_kill` 对怪和人都调，
        #   所以 `kills@族号` 天然也是「人 + 怪」，和上面的 `kills` 一个口径。
        for name in ("shots", "kills", "damage"):
            value = int(cell.get(name, 0))
            if value > 0:
                out[stat_key(name, roh)] = value
        # ★ 「某武器命中数」没有单独的计数器（`rpExplode` 里没有武器 id，
        #   归账靠的是「最近一发开的什么枪」）—— 所以 `hits` / `splash_hits`
        #   在 `CARD_METRICS` 里标的是「分不了武器」。
    return dict((key, value) for key, value in out.items() if value)


def _cell(row, seat):
    try:
        return int(row[seat])
    except (IndexError, TypeError, ValueError):
        return 0


def due_grants(rules, *, mode, stage, difficulty, match, total, bases):
    """该发这个玩家几张卡。返回 `({卡片: 张数}, {卡片: 新的计数器基准}, [警告])`。

    * `rules`  —— `shopcfg.cards()` 读出来的那一份
    * `mode`   —— 这一局是 `"pvp"` 还是 `"quest"`
    * `match`  —— `match_stats()` 算出来的本局战绩
    * `total`  —— 这个账号**加上本局之后**的累计战绩
    * `bases`  —— `{卡片 id 字符串: {统计键: 上次归零时的累计值}}`

    第二个返回值只有**真的发了卡而且那条规则带累计条件**的卡片才有：
    存档层拿它把「计数器归零」这件事落盘（用户 2026-09-13 原话：
    「发过奖励后，两个计数器同时归零，重新开始新一轮计数」）。
    存档层完全不认识规则，也就没有回调和重入。

    ★★ **一次结算最多发一张**：归零之后每个累计条件的进度都是 0，
    表达式当场就不成立了 ⇒ 不需要循环、也不需要「上限」那一格来兜底。
    """
    give = {}
    new_bases = {}
    warnings = []
    for rule in rules or ():
        if not rule.get("listed"):
            continue
        card = rule.get("card")
        if card is None:
            continue
        if not _applies(rule, mode=mode, stage=stage, difficulty=difficulty):
            continue
        base = (bases or {}).get(str(card)) or {}
        holds, used, bad = eval_conditions(
            rule.get("conditions"), rule_mode=rule.get("mode"),
            match=match, total=total, base=base)
        for note in bad:
            warnings.append("卡片 %s 的规则：%s" % (card, note))
        if not holds:
            continue
        give[card] = give.get(card, 0) + shopcfg.CARD_GRANT_COUNT
        if used:
            # 这条规则带累计条件 ⇒ 发了就把那几个计数器的基准挪到当前值。
            new_bases[card] = used
    return give, new_bases, warnings


#: 判定日志里那两个记号。★ 写成常量，别在拼字符串时各写各的。
OK_MARK = "✓"
NO_MARK = "✗"


def explain(rules, *, mode, stage, difficulty, match, total, bases):
    """这一次判定**为什么给 / 为什么不给**，逐条说成人话（V0.3商店）。

    返回一串不带缩进的行，调用方自己排版。**只给调试日志用**
    （`gameserver.send_end_game()` 在 `--verbose` 下才打）——
    一局 17 张卡 × 六个人，正常模式下打出来没人看得完。

    ★★ **和 `due_grants` 走同一套谓词**（`_applies` / `eval_conditions`），
    不是另写一份判定：日志和真发的卡出自两次不同的计算，迟早会出现
    「日志说该发、玩家没收到」那种查不动的事。
    ⚠ 但它是**另一次**求值 —— 只读，不改任何状态（`used` 收在本地丢掉），
    所以放在 `due_grants` 后面调用不会重复发卡。
    """
    lines = []
    for rule in rules or ():
        card = rule.get("card")
        if card is None:
            continue
        head = "%s #%s" % (shopcfg.item_name(card) or "?", card)
        if not rule.get("listed"):
            lines.append("%s  %s 关着（「能获得」= 否）" % (head, NO_MARK))
            continue
        why = _why_not_applies(rule, mode=mode, stage=stage,
                               difficulty=difficulty)
        if why:
            lines.append("%s  %s 这一局不算 —— %s" % (head, NO_MARK, why))
            continue
        seen = []
        holds, used, warnings = eval_conditions(
            rule.get("conditions"), rule_mode=rule.get("mode"),
            match=match, total=total,
            base=(bases or {}).get(str(card)) or {}, seen=seen)
        lines.append("%s  %s" % (head, ("%s 发 %d 张"
                                        % (OK_MARK, shopcfg.CARD_GRANT_COUNT))
                                 if holds else ("%s 不发" % NO_MARK)))
        for note in warnings:
            lines.append("    ⚠ %s" % note)
        for at, note in enumerate(seen):
            cond = note["cond"]
            join = ("" if at == 0 else
                    shopcfg.CARD_JOIN_ZH.get(cond.get("join",
                                                      shopcfg.CARD_JOIN_AND),
                                             "") + " ")
            if "now" in note:
                # 累计：说清这一轮攒了多少、以及它是从哪个基准起算的 ——
                # 「累计 143 却只算 43」一眼看不出来就会被当成掉数。
                got = ("这一轮 %d/%d（累计 %d − 基准 %d）"
                       % (note["value"], note["threshold"], note["now"],
                          note["base"]))
            else:
                got = "本局 %d（要 %d）" % (note["value"], note["threshold"])
            lines.append("    %s%s %s  %s"
                         % (join, shopcfg.card_condition_text(cond),
                            OK_MARK if note["holds"] else NO_MARK, got))
        if holds and used:
            lines.append("    ★ 计数器归零 → %s"
                         % "、".join("%s=%d" % (k, used[k])
                                     for k in sorted(used)))
    return lines


def _why_not_applies(rule, *, mode, stage, difficulty):
    """「对局模式」那三格是哪一格没对上；对上了就返回 `""`。

    ★★ **`_applies()` 就是它**（`not _why_not_applies(...)`）—— 判定和
    「为什么不算」出自同一组比较，想drift都drift不了。
    ★ 对上的那一支一个字符串都不拼（直接 `return ""`），所以结算路径上
      没有多余开销；拼字符串只发生在「这一局不算」那一支。
    """
    want = rule.get("mode")
    if want is not None and want != mode:
        return ("规则限「%s」，本局是「%s」"
                % (shopcfg.CARD_MODE_ZH.get(want, want),
                   shopcfg.CARD_MODE_ZH.get(mode, mode)))
    if rule.get("stage") is not None and rule["stage"] != stage:
        return "规则限关卡 %s，本局是 %s" % (rule["stage"], stage)
    if rule.get("difficulty") is not None and rule["difficulty"] != difficulty:
        return "规则限难度 %s，本局是 %s" % (rule["difficulty"], difficulty)
    return ""


def card_progress(rules, *, stats, bases, granted=None):
    """每张卡片「**离下一次拿到还差多少**」（用户 2026-09-13 第四轮）。

    管理页两处弹窗（玩家仓库那一行的「卡片进度」、称号卡片页的
    「查看本人达成进度」）画的就是这一份 —— **同一个出处**，两处长一个样。

    返回 `[{card, listed, text, granted, conditions: [...]}]`，其中每条条件：

        {scope, text}                 一局内 —— 进度是「每局现算」，攒不住
        {scope, text, have, need}     玩家累计 —— 这一轮攒了多少 / 要多少

    ★ `have` 是**这一轮**攒的（累计值 − 上次归零时的基准），不是账号总数：
      页面上问的是「还差多少」，而计数器在拿到卡的那一刻就归零了。
    """
    out = []
    for rule in rules or ():
        card = rule.get("card")
        if card is None:
            continue
        base = (bases or {}).get(str(card)) or {}
        rows = []
        for cond in rule.get("conditions") or ():
            metric = cond.get("metric")
            scope = cond.get("scope", shopcfg.CARD_SCOPE_MATCH)
            row = {"scope": scope, "text": shopcfg.card_condition_text(cond)}
            if scope == shopcfg.CARD_SCOPE_TOTAL:
                weapon = cond.get("weapon")
                if not shopcfg.card_metric_needs_weapon(metric):
                    weapon = None
                now = stat_of(stats, rule.get("mode"), metric, weapon)
                row["have"] = max(0, now - int(base.get(
                    stat_key(metric, weapon), 0)))
                row["need"] = max(shopcfg.card_op_min(shopcfg.CARD_OP_GE),
                                  int(cond.get("threshold", 1)))
            rows.append(row)
        out.append({
            "card": card,
            "listed": bool(rule.get("listed")),
            "text": shopcfg.describe_card_rule(rule),
            # 「已经拿过几张」—— 「直到**下一次**获得」这句话本身就预设了
            # 前面可能已经拿过，不说的话看的人没有参照。
            "granted": int((granted or {}).get(str(card), 0)),
            "conditions": rows,
        })
    return out


def eval_conditions(conditions, *, rule_mode, match, total, base, seen=None):
    """算这一串条件成不成立。返回 `(成不成立, {统计键: 当前累计值}, [警告])`。

    * 第二项是**这条规则用到的累计计数器现在读到多少** —— 发了卡就拿它
      当新的基准（「归零」）。⚠ 它**不受短路影响**：`A 或者 B` 里 `A`
      成立时 `B` 也要收进来，否则那个计数器会一直攒下去永远不清。
    * **从上往下依次结合，没有括号**（用户拍板）：`A 或者 B 并且 C`
      = `(A 或者 B) 并且 C`。说明文那边（`shopcfg.card_conditions_text`）
      混用连接词时会把括号写出来，两边是同一个口径。

    ★ `seen` 给一个列表时，**每条条件**算完往里塞一份「算了什么、得几、
      成不成立」（`explain()` 拿它打调试日志）。判定本身一个分支都不变 ——
      日志和决策必须出自**同一次**求值，各算一遍迟早对不上。
    """
    conditions = list(conditions or ())
    used = {}
    warnings = []
    if not conditions:
        # 一条条件都没有的规则**不发卡**：那等于「每局白送」，而画面上
        # 那句说明写的正是「条件无效」。
        return False, used, ["一条达成条件都没有，不发卡"]
    value = None
    for at, cond in enumerate(conditions):
        metric = cond.get("metric")
        if metric not in shopcfg.CARD_METRICS:
            warnings.append("认不出的指标 %r，整条规则跳过" % (metric,))
            return False, used, warnings
        got = _condition_holds(cond, rule_mode=rule_mode, match=match,
                               total=total, base=base, used=used, seen=seen)
        if at == 0:
            value = got
        elif cond.get("join") == shopcfg.CARD_JOIN_OR:
            value = value or got
        else:
            value = value and got
    return bool(value), used, warnings


def _condition_holds(cond, *, rule_mode, match, total, base, used, seen=None):
    """一条条件成不成立。顺带把**累计**那一档现在读到多少记进 `used`。"""
    metric = cond.get("metric")
    op = cond.get("op", shopcfg.CARD_OP_GE)
    # ★★ 下限**跟着比较符走**，别写死 `max(1, …)`：写死的话
    #   「死亡次数 等于 0」会被悄悄提成「等于 1」—— 那条卡就成了
    #   「死一次才给」，而页面上、日志里都看不出来。
    #   （校验器那边是同一张 `CARD_OP_MIN`，这儿只是兜手改进来的文件。）
    threshold = max(shopcfg.card_op_min(op), int(cond.get("threshold", 1)))
    weapon = cond.get("weapon")
    if not shopcfg.card_metric_needs_weapon(metric):
        weapon = None
    note = {"cond": cond, "threshold": threshold}
    if cond.get("scope") != shopcfg.CARD_SCOPE_TOTAL:
        value = _match_value(match, metric, weapon)
        holds = card_op_holds(op, value, threshold)
    else:
        # 累计：**这一轮攒了多少** = 现在的累计值 − 上次归零时的累计值。
        # ★ 取不到基准就按 0 算（第一轮）；基准比现在大（运营改过「模式」
        #   那一格之后会出现）按 0 算，不让进度变成负数。
        now = stat_of(total, rule_mode, metric, weapon)
        key = stat_key(metric, weapon)
        used[key] = now
        value = max(0, now - int(base.get(key, 0)))
        note["now"] = now
        note["base"] = int(base.get(key, 0))
        holds = card_op_holds(op, value, threshold)
    if seen is not None:
        note["value"] = value
        note["holds"] = holds
        seen.append(note)
    return holds


def _match_value(match, metric, weapon):
    """本局战绩里那一个数。

    ⚠ **取不到就是 0** —— 这正是「小于等于 / 等于」不许配武器维度的原因
    （`shopcfg.validate_cards` 拦着）：没拿过那把枪的人 `kills@族号` 根本
    不存在，「只算左轮打出的击杀数为 0」会对绝大多数人成立。

    ★ 命中率现算，和累计那一侧（`stat_of`）同一个口径。
    """
    if metric == "accuracy":
        shots = int((match or {}).get(stat_key("shots", weapon), 0))
        if shots <= 0:
            return 0
        return int((match or {}).get(stat_key("hits", weapon), 0)) * 100 // shots
    return int((match or {}).get(stat_key(metric, weapon), 0))


def _applies(rule, *, mode, stage, difficulty):
    """这条规则管不管这一局。**只看「对局模式」那个弹窗里的三格**。

    达成条件那一段不在这儿 —— 它要按每条条件各自的 `scope` 分成
    「本局」和「累计」两条路（`eval_conditions`）。

    ★ 实现**就是** `_why_not_applies()`：调试日志要说出「哪一格没对上」，
      两处各写一组比较迟早会不一致。
    """
    return not _why_not_applies(rule, mode=mode, stage=stage,
                                difficulty=difficulty)
