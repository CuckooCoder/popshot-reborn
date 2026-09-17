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

★ **她自带的音效只有一个**（FINDINGS §24，对着原版解密树逐目录数过）：
`Sounds/CharacterChanger/ch003.ogg`（选人界面念她名字）。
`FX/wp/ch03/`、`FX/wplite/ch03/`、`FX/char/KR/char/ch03/`、`FX/char/KR/voice/ch03/`
在原版里**一个都不存在** ⇒ 下面这些**全是借的**，不是「找回了她自己的」。

## 挑音源的依据

不是随便找个声音顶上，是**按武器的结构角色**配的 —— 每条都有理由：

| 她的武器 | 类 | 配了谁 | 为什么 |
|---|---|---|---|
| 트릭스터 Trickster | `GeneralBullet` 轻型连射 | 泰尔的左轮 | 🔴 换过 발키리的机枪又被听回来了，见下 |
| 씨드 폭탄 Seed Bomb | `SeedBomb` 蓄力投掷 | 泰尔的分裂手雷 | 同样是「扔出去 + 炸开成小块」的结构 |
| └ 爆炸 | | `Weapon-Hit-Grenade` | 手雷爆炸 |
| └ 벌레（蝴蝶碎片） | `Splinter` | `Weapon-Hit-ApplePiece` | ★ 原版 `ch00-02a` 用的就是它，**同一个「碎片」角色** |
| 에이리얼 슈터 Aerial Shooter | `TotemLauncher` | ★ **`FX/wp/common/weapon@watergun.ogg`** | 见下 |
| └ 落地（`-set`） | | `Weapon-Hit-Bottle` | 她发射的是**포션（药水）**，瓶子落地的轻响 |
| └ 回血（`-hpup`） | | **`HpCharge`** | ★ 原版就是「HP 充能」音，正对她的回血图腾 |
| └ 命中（`-hit`） | | `Weapon-Hit2-Bottle` | 瓶子碎裂；原版 `ch01-02a`（燃烧瓶）用的同一个 |

### 3 号武器换过一次（用户 2026-09-16 实机：「和卡希尔的一样」）

**原先**配的是卡希尔的 `ch01@Attack03.ogg`（캐논왈츠 火箭炮）—— 理由是「同样是发射器」，
但那是**一发炮**：1.53 s、带低频轰鸣尾巴，和她那把「把药水抛出去」的东西差得远，
而且卡希尔就在旁边打，一听就是同一发声音。

**现在**配 `FX/wp/common/weapon@watergun.ogg` —— **原版发了但一个地方都没引用的音**
（`Data/` 全部 ini、61 个 `.evn`、全部 `.efx`、exe 里都搜不到它；`FX/wp/common/` 整个目录
就这一个文件）。按铁律 13「先搜，再动手造」，这是现成的。

客观对上的几条：**0.51 s**（对 `LoadingTime=300` / `ReloadTime=5300` 的单发慢抛正好，
卡希尔那 1.53 s 会拖到装填里）、低频只占 11%（不是炮，是一声轻快的水声）、
起音 0.021 s（抛出去的瞬间）、质心 2767 Hz。**「水枪」正对她发射的 포션（药水）。**

`_Sound-Fire`（泡泡模式）也用同一个：原版 `[ch03-03a]` 自己的 `_Sound-Bounce` / `_Sound-Hit`
就是直接指非 lite 的那份，这条武器上「lite 复用普通音」是原版自己的写法。

### 🔴 1 号武器：换成 발키리的机枪试过了，**用户听完否掉，换回泰尔** —— 别再动

2026-09-17 把它换成 `ch102@Attack01.ogg`（발키리 아머드 머신건），当天用户实机听完说
**「新的不好听」**，已经**改回 `ch00@Attack01.ogg`**（泰尔 리볼버）。

**当时换的两条理由都还成立，但都输给「听着好听」**（口径见 D17）：

① **射速对不上**：`[CH03-01]` 的 `CoolingTime=90` 是**全游戏最快的连射枪**
（발키리 130 / 프로코 140 / 泰尔 **220**），而左轮那条**有效 0.55 s**，90 ms 一发叠六层；
② **泰尔是基础角色、天天同屏** —— 正撞在「别借同屏角色的招牌音」上（§24）。

⇒ **这两条现在是「已知代价」，不是待办**。下一个会话别再「顺手修正」它：
数值上 발키리那条确实更短更亮（0.41 s / 有效 0.30 / 质心 1376，对 0.77 / 0.55 / 1076），
**但人耳投票已经投过了**。真要再动，先问用户。

⚠ **2 号武器同样借泰尔（ch00）的，也别动** —— 三个角色的投掷音几乎是同一个声
（泰尔 0.17 s / 843 Hz、卡希尔 0.16 / 809、艾丽亚丝 0.15 / 777），听不出区别。

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
    # 1 号 트릭스터 —— 轻型连射。★ 用泰尔的左轮，**别再换成 발키리的机枪**（见上面那节）
    "FX/wp/ch03/ch03@Attack01.ogg":        "ch00@Attack01.ogg",
    "FX/wplite/ch03/ch03@Attack01_Lite.ogg": "FX/wplite/ch00/ch00@Attack01_Lite.ogg",
    # 2 号 씨드 폭탄 —— 蓄力投掷 + 炸开成蝴蝶
    "FX/wp/ch03/ch03@Attack02.ogg":        "ch00@Attack02.ogg",
    "FX/wp/ch03/ch03@Attack02-hit.ogg":    "Weapon-Hit-Grenade.ogg",
    "FX/wp/ch03/ch03@Attack02-bug.ogg":    "Weapon-Hit-ApplePiece.ogg",
    "FX/wplite/ch03/ch03@Attack02_Lite.ogg": "FX/wplite/ch00/ch00@Attack02_Lite.ogg",
    # 3 号 에이리얼 슈터 —— 发射回血图腾（포션）
    # ★ 发射音是原版发了却没人引用的那个「水枪」，不是卡希尔的火箭炮（见上面那节）
    "FX/wp/ch03/ch03@Attack03.ogg":        "FX/wp/common/weapon@watergun.ogg",
    "FX/wp/ch03/ch03@Attack03-set.ogg":    "Weapon-Hit-Bottle.ogg",
    "FX/wp/ch03/ch03@Attack03-hpup.ogg":   "HpCharge.ogg",
    "FX/wp/ch03/ch03@Attack03-hit.ogg":    "Weapon-Hit2-Bottle.ogg",
    "FX/wplite/ch03/ch03@Attack03_Lite.ogg": "FX/wp/common/weapon@watergun.ogg",
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
    # ★ 把 stdout / stderr 钉成 utf-8。调用方一**捕获**输出（管道 / 赋值给变量），
    #   CPython 就发现 stdout 不是控制台、改用 `GetACP()` = cp936 —— 中文按 GBK 落进
    #   管道而上游按 utf-8 解（满屏乱码），`✓` 这种 cp936 编不出来的字符更是直接
    #   `UnicodeEncodeError` 把进程带崩。完整来龙去脉见 `tools/pkn.py` 的 main()。
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
