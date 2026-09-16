#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ch03_sounds.py —— 给爱琳（ch03）补上武器音效（X_Mod · X1）。

    python tools/ch03_sounds.py            # 落盘
    python tools/ch03_sounds.py --dry-run
    python tools/ch03_sounds.py --check    # 只查缺不缺，不写

## 为什么缺（根因）

`Data/weapon.ini` 里三个基础角色和爱琳用的是**两种写法**：

    [ch01-01]  Sound-Fire=ch01@Attack01.ogg                 ← 裸文件名，相对 Sounds/ 根
    [CH03-01]  Sound-Fire=FX/wp/ch03/ch03@Attack01.ogg      ← 全路径

前者的文件就躺在 `Sounds/` 根目录里；后者指向的 `Sounds/FX/wp/ch03/` 原版**从没发过**
（它本该和 `Models/Characters/ch03/` 一起发，而那个目录整个是缺的，FINDINGS §1）。
⇒ 她开枪没声。**不改 `weapon.ini`**（那是原版数据），补文件即可。

## 挑音源的依据

不是随便找个声音顶上，是**按武器的结构角色**配的 —— 每条都有理由：

| 她的武器 | 类 | 配了谁 | 为什么 |
|---|---|---|---|
| 트릭스터 Trickster | `GeneralBullet` 轻型连射 | 泰尔的手枪 | 三个基础角色里手枪最轻，配花瓣小枪；卡希尔是双散弹、布洛克是机枪，都太重 |
| 씨드 폭탄 Seed Bomb | `SeedBomb` 蓄力投掷 | 泰尔的分裂手雷 | 同样是「扔出去 + 炸开成小块」的结构 |
| └ 爆炸 | | `Weapon-Hit-Grenade` | 手雷爆炸 |
| └ 벌레（蝴蝶碎片） | `Splinter` | `Weapon-Hit-ApplePiece` | ★ 原版 `ch00-02a` 用的就是它，**同一个「碎片」角色** |
| 에이리얼 슈터 Aerial Shooter | `TotemLauncher` | 卡希尔的追踪火箭 | 同样是「发射出去、落地生效」的发射器 |
| └ 落地（`-set`） | | `Weapon-Hit-Bottle` | 她发射的是**포션（药水）**，瓶子落地的轻响 |
| └ 回血（`-hpup`） | | **`HpCharge`** | ★ 原版就是「HP 充能」音，正对她的回血图腾 |
| └ 命中（`-hit`） | | `Weapon-Hit2-Bottle` | 瓶子碎裂；原版 `ch01-02a`（燃烧瓶）用的同一个 |

★ **这是可以改的**：不满意就改下面 `MAPPING` 里的一行再跑一遍，幂等。

## 幂等

按字节比较，只写不同的；第二遍输出「0 写入」。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOUNDS = os.path.join(ROOT, "game_patched", "Pack_develop", "Sounds")

#: `目标（相对 Sounds/）` -> `音源（相对 Sounds/）`
#: 理由见模块说明的那张表。改这里就能换音，不用动 weapon.ini。
MAPPING = {
    # 1 号 트릭스터 —— 轻型连射
    "FX/wp/ch03/ch03@Attack01.ogg":        "ch00@Attack01.ogg",
    "FX/wplite/ch03/ch03@Attack01_Lite.ogg": "FX/wplite/ch00/ch00@Attack01_Lite.ogg",
    # 2 号 씨드 폭탄 —— 蓄力投掷 + 炸开成蝴蝶
    "FX/wp/ch03/ch03@Attack02.ogg":        "ch00@Attack02.ogg",
    "FX/wp/ch03/ch03@Attack02-hit.ogg":    "Weapon-Hit-Grenade.ogg",
    "FX/wp/ch03/ch03@Attack02-bug.ogg":    "Weapon-Hit-ApplePiece.ogg",
    "FX/wplite/ch03/ch03@Attack02_Lite.ogg": "FX/wplite/ch00/ch00@Attack02_Lite.ogg",
    # 3 号 에이리얼 슈터 —— 发射回血图腾（포션）
    "FX/wp/ch03/ch03@Attack03.ogg":        "ch01@Attack03.ogg",
    "FX/wp/ch03/ch03@Attack03-set.ogg":    "Weapon-Hit-Bottle.ogg",
    "FX/wp/ch03/ch03@Attack03-hpup.ogg":   "HpCharge.ogg",
    "FX/wp/ch03/ch03@Attack03-hit.ogg":    "Weapon-Hit2-Bottle.ogg",
    "FX/wplite/ch03/ch03@Attack03_Lite.ogg": "FX/wplite/ch01/ch01@Attack03_Lite.ogg",
}


def _abs(rel):
    return os.path.join(SOUNDS, rel.replace("/", os.sep))


def main(argv=None):
    ap = argparse.ArgumentParser(description="给爱琳补武器音效")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划")
    ap.add_argument("--check", action="store_true", help="只查缺不缺")
    args = ap.parse_args(argv)

    missing_src = [s for s in MAPPING.values() if not os.path.exists(_abs(s))]
    if missing_src:
        print("音源不存在，先查清楚：")
        for s in missing_src:
            print("   " + s)
        return 1

    if args.check:
        gone = [d for d in MAPPING if not os.path.exists(_abs(d))]
        print("爱琳武器音：%d 个，缺 %d 个" % (len(MAPPING), len(gone)))
        for d in sorted(gone):
            print("   缺 " + d)
        return 1 if gone else 0

    written = skipped = 0
    for dst, src in sorted(MAPPING.items()):
        sp, dp = _abs(src), _abs(dst)
        blob = open(sp, "rb").read()
        if os.path.exists(dp) and open(dp, "rb").read() == blob:
            skipped += 1
            continue
        print("%s  <-  %s" % (dst, src))
        if not args.dry_run:
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            shutil.copyfile(sp, dp)
        written += 1

    print("%s写入 %d / 未变 %d" % ("[dry-run] " if args.dry_run else "", written, skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
