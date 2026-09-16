#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""anim.py —— 爱琳的持枪 / 开枪 / 换弹 / 跑动 / **冲刺攻击**动作：从瓦尔基里（ch102）移植。

    C:\\Python314\\python.exe tools/ch03_skin/anim.py                # 重建 ch03 全部 86 个 .mtn（幂等）+ 渲对照图
    C:\\Python314\\python.exe tools/ch03_skin/anim.py --dry-run      # 只算只渲，不写
    C:\\Python314\\python.exe tools/ch03_skin/anim.py --no-render

## 在重建链里的位置

    mkchar.py（克隆母本）→ recolor.py → ears.py → geometry.py → weapons.py → **anim.py（本脚本）**

本脚本**只读**只读母本 ch01 的 86 个 `.mtn`（爱琳的骨架和它逐字节相同）和 ch102 的 5 个源动作，
不读 ch03 里任何被改过的东西 ⇒ 幂等。

## 为什么是「移植旋转」而不是「直接拷文件」（FINDINGS §19）

`.mtn` 里带着整棵骨架树（229 节点，含每根骨的静态局部矩阵）。瓦尔基里的树只有 169 节点、
身材也大一号（DisplayHeight 90 vs 75），直接拷过来：① 爱琳网格引用的头发 / 裙摆骨在她树里不存在 ⇒
按名字查骨返回 NULL 喂进矩阵乘直接崩（V0.3商店 §50）；② 平移关键帧带着她的骨长，会把爱琳的手臂拉长。
两套骨架的共同部分都是 3ds Max Biped 的标准命名（`Bip01_*`），局部旋转的约定一致 ⇒
**只抄旋转、平移 / 缩放留爱琳自己的**，姿势就过来了，骨长不变。

## 规则

每条动作自己选「抄哪些骨的旋转」（`CLIPS` 的第三项）：

| 口径 | 抄旋转的骨 | 用在哪 |
|---|---|---|
| `take_upper` | 上半身 `UPPER`（Spine1 以上 + 双臂 + 手指） | 持枪 5 个动作 + Idle101 —— 下半身是走 / 跑 / 站，腿留爱琳自己的最稳 |
| `take_biped` | **全部 45 根 `Bip01_*`**（含根骨 / 骨盆 / 脊柱 / 双腿） | `Dash00` 冲刺攻击 —— 那是整个身体扑出去，只换上半身会变成「瓦尔基里的上身 + 卡希尔的抱熊步」 |

**平移 / 缩放一律留爱琳自己的**（母本 ch01 同名动作）—— 平移键里带着骨长，抄过来手臂会被拉长（§20）。
`Dash00` 是腾空扑击，脚不落地，所以 D9 ④「腿长不同、脚会浮起」那条顾虑在这一条上不成立。

时长：Stand01 / Reload01 两边本来就相等；Run-F01 / Run-B01 取爱琳的 0.667 s（跑步循环速度和她的
`ChrSpeed` 是配套的，ch102 的 0.8 s 上半身重采样过来）；Attack01 取 ch102 的 0.1 s（步枪后坐，
对应 `CoolingTime=90`；卡希尔那个 0.667 s 是双枪轮射，留着会让每一枪都做半套挥枪）；
**Dash00 取爱琳自己的 1.0 s**（= 30 帧）—— `ChrProps.ini` 里她自己的 `Dash00-TotalFrame=31`，
用 ch102 的 0.833 s（25 帧）会在技能末尾定格 6 帧。

## 静态骨补丁（`STATIC_PATCHES`）

武器骨链全是静态骨（没有轨道，位置写死在树里，86 个文件一样）。引擎按名字找
`Bone_wp01_firepoint`、找不到才找 `Bone_wp01_R_firepoint`（`0x506b74` / `0x506ba7`），
左手那个 `_L_FirePoint` 根本不用。步枪的枪口在哪，`Bone_Wp01_R_FirePoint` 就得挪到哪，
否则枪火 / 弹道线从空气里冒出来。补丁对 86 个文件一起打，树保持一致。

## 事件文件（`EVN_FROM_SRC` / `EVN_RES_REMAP`）

动作换了，挂在动作上的音效 / 特效也得跟着换：

- `Reload01` 整份用瓦尔基里的（她只在第 21 帧响一次拉栓声，卡希尔那份是双枪两次上膛）。
- `Dash00` 把两条特效从 `CH01/WP00/` 改指 **`CH03/WP00/`** —— 原版**本来就发了**爱琳自己的
  `CH03_DashAttack00.efx`（挂 `Bip01_R_Hand`，花瓣）和 `CH03_DashDust00.efx`（铁律 12）；
  而克隆来的 CH01 版挂在 **`Bone_Wp00_11`** —— 卡希尔那只熊身上的骨，爱琳没有熊，
  特效就从空气里冒出来。音效两条（`ch01@dash_.ogg` / `ch01@Dash.ogg`）是通用风声，留着。
- 音效同走 `EVN_RES_REMAP`（`PlaySound` 的路径相对 `Sounds/`）。目前只改了 3 号武器的换弹。

⚠ **爱琳一个自带音都没有**（§24）：她的 `.evn` 里 83 处音频引用有 **80 处是卡希尔的** ——
连受击闷哼、格斗吆喝（`ch01@Voice01..03`）、跑步脚步声都是。这里只挑用户实机点名的换掉，
**没做全面替换** —— 换成谁是口味问题，得用户拍板（PROGRESS「下一步」里记着）。
"""
from __future__ import annotations

import argparse
import glob
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
sys.path.insert(0, HERE)
import mtntool  # noqa: E402

CHARS = os.path.join(ROOT, "game_patched", "Pack_develop", "Models", "Characters")
CH01 = os.path.join(CHARS, "ch01")
CH03 = os.path.join(CHARS, "ch03")
CH102 = os.path.join(CHARS, "ch102")
EFFECTS = os.path.join(ROOT, "game_patched", "Pack_develop", "Effects")
SOUNDS = os.path.join(ROOT, "game_patched", "Pack_develop", "Sounds")
TPF = mtntool.TICKS_PER_FRAME


def src_mtn(ch, clip):
    """源动作文件（只读别的角色目录）。"""
    return mtntool.parse(os.path.join(CHARS, ch, "%s@%s.mtn" % (ch, clip)))

UPPER = {"Bip01_Spine1", "Bip01_Spine2", "Bip01_Neck", "Bip01_Head", "Bip01_HeadNub"}
for side in "LR":
    for b in ("Clavicle", "UpperArm", "Forearm", "Hand", "Finger0", "Finger01", "Finger0Nub",
              "Finger1", "Finger11", "Finger1Nub", "Finger2", "Finger21", "Finger2Nub"):
        UPPER.add("Bip01_%s_%s" % (side, b))


def take_upper(name):
    """只抄上半身。下半身（走 / 跑 / 站）留爱琳自己的。"""
    return name in UPPER


def take_biped(name):
    """整套 Biped 都抄。★ 前缀判据同时挡掉了 `Bone_Wp*` / `Bone_Robe_*` ——
    那是瓦尔基里自己的武器件骨和长袍骨，抄过来对不上爱琳的骨架。"""
    return name.startswith("Bip01")


# 爱琳动作名 -> 怎么移植
#   src / clip  源角色和源动作      dur  时长取谁（"dst" 爱琳自己的 / "src" 源的）
#   take        抄哪些骨的旋转      sync_legs  要不要按爱琳自己的腿相位对齐源的摆臂（见 leg_sync_phase）
CLIPS = {
    "Stand01": dict(src="ch102", clip="Stand01", dur="dst", take=take_upper),
    "Attack01": dict(src="ch102", clip="Attack01", dur="src", take=take_upper),
    "Reload01": dict(src="ch102", clip="Reload01", dur="dst", take=take_upper),
    "Run-F01": dict(src="ch102", clip="Run-F01", dur="dst", take=take_upper),
    "Run-B01": dict(src="ch102", clip="Run-B01", dur="dst", take=take_upper),
    # 持枪发呆：ch102 2.667 s，爱琳自己的下半身 / 头发轨道按 1.667 s 循环铺满
    "Idle101": dict(src="ch102", clip="Idle101", dur="src", take=take_upper),
    # ★ 冲刺攻击（`[ch03-dash]` 的 `WAnimIdx=0` ⇒ 放 `Dash00`）。卡希尔这一下是**抱着泰迪熊撞过去**
    #   （`ch01W0000` 就是那只熊，`[ch01-dash]` 没写 `WMeshIdx` ⇒ 默认取 0 号网格），
    #   而 `[ch03-dash]` 写的是 `WMeshIdx=-1`（不拿东西）—— 爱琳照搬那套动作就成了「抱空气」。
    #   瓦尔基里这一下是腾空扑击、`[ch102-dash]` 同样 `WMeshIdx=-1`，正对爱琳自己的
    #   `Dash00-DamagingObjBone=Bip01_R_Finger1`（右手打人，不是靠熊）。
    "Dash00": dict(src="ch102", clip="Dash00", dur="dst", take=take_biped),
    # ★ 空手跑（同样是 `WAnimIdx=0` ⇒ 房间里、格斗模式下跑动放的是 `Run-F00`）。
    #   卡希尔这一套是**单手托着熊跑**：左右上臂摆幅 58.5°/49.0°、前臂 34.3°/19.3°，
    #   右前臂几乎不动 —— 手里没熊就成了「拎着个看不见的东西」（用户 2026-09-16 实机）。
    #   **源取 ch00 泰尔，不是瓦尔基里**：ch102 的 Run-F00 是她把步枪架在左臂上跑，
    #   左右差 78.8° 是全表最不对称的一个，抄过来只会更糟；ch00 的前臂摆幅左右**恰好相等**，
    #   而且 `DisplayHeight` 和爱琳一样是 75（§23）。
    "Run-F00": dict(src="ch00", clip="Run-F00", dur="dst", take=take_upper, sync_legs=True),
}

# 静态骨补丁：骨名 -> 新的 4x4 局部矩阵。由 rig.py 从步枪 / 手杖的设计框架算出（BarrelPoint / _03 / FirePoint 三根一链），
# 在 main() 里懒加载（rig 反过来要用本文件的 retarget，避免循环导入）。
STATIC_PATCHES = {}

# 事件文件：换弹动作换成瓦尔基里的（她那份只在第 21 帧响一次拉栓声，卡希尔的是双枪两次上膛）
EVN_FROM_SRC = {"Reload01": "Reload01"}

#: 事件文件里的资源改指：母本 ch01 的 `<ResourcePath>` -> 换成谁。**对全部 61 个 `.evn` 生效**，
#: 在母本上按 `<ResourcePath>整段</ResourcePath>` 替换后写进 ch03（幂等，母本只读）。
#: 两类都走这里：`RenderEffect` 的路径相对 `Effects/`，`PlaySound` 的相对 `Sounds/`。
#: 只许改成**磁盘上真实存在**的文件，main() 会逐条核对；
#: 改完 `mkchar --audit` 第 ⑤ 条（死引用不许变多）会再兜一道。
#:
#: 特效那批是这么来的：把母本 61 个 `.evn` 引用的 24 个特效逐个去 `Effects/` 里找 CH03 版，
#: **有就换，没有就留着**。没有 CH03 版的是 `Jab00` / `DashAttack01..05` / `Dash04` / `Dash-B04`
#: （原版就没给爱琳画），只能继续用卡希尔的。
#: ⚠ `CH01_MutuDust` 的 CH03 版多个 `00` 后缀，不是机械替换串能对上的。
EVN_RES_REMAP = {
    # 冲刺攻击 + 格斗：CH01 版里有 8 个挂在 `Bone_Wp00_04` / `Bone_Wp00_11` ——
    # 卡希尔那只泰迪熊身上的骨。爱琳没有熊，特效就从身侧的空气里冒出来。
    # CH03 版挂的是 `Bip01_R_Hand` / `Bip01_R_Toe0` 这些她自己身上的骨。
    "CH01/WP00/Efx/CH01_DashAttack00.efx": "CH03/WP00/Efx/CH03_DashAttack00.efx",
    "CH01/WP00/Efx/CH01_DashDust00.efx": "CH03/WP00/Efx/CH03_DashDust00.efx",
    "CH01/WP00/Efx/CH01_MutuDust.efx": "CH03/WP00/Efx/CH03_MutuDust00.efx",
    "CH01/WP00/Efx/CH01_MutuCrunch-K00.efx": "CH03/WP00/Efx/CH03_MutuCrunch-K00.efx",
    "CH01/WP00/Efx/CH01_MutuCrunch-P00.efx": "CH03/WP00/Efx/CH03_MutuCrunch-P00.efx",
    "CH01/WP00/Efx/CH01_MutuJump-P00.efx": "CH03/WP00/Efx/CH03_MutuJump-P00.efx",
    "CH01/WP00/Efx/CH01_MutuStand-K00.efx": "CH03/WP00/Efx/CH03_MutuStand-K00.efx",
    "CH01/WP00/Efx/CH01_MutuStand-K01.efx": "CH03/WP00/Efx/CH03_MutuStand-K01.efx",
    "CH01/WP00/Efx/CH01_MutuStand-P00.efx": "CH03/WP00/Efx/CH03_MutuStand-P00.efx",
    "CH01/WP00/Efx/CH01_MutuStand-P01.efx": "CH03/WP00/Efx/CH03_MutuStand-P01.efx",
    "CH01/WP00/Efx/CH01_MutuStand-P02.efx": "CH03/WP00/Efx/CH03_MutuStand-P02.efx",
    "CH01/WP00/Efx/CH01_MutuStand-P03.efx": "CH03/WP00/Efx/CH03_MutuStand-P03.efx",
    # ★ 格斗普通拳 `Jab00`：**没有同名的 CH03 版**，但也不能留着卡希尔那份 ——
    #   `CH01_Jab00.efx` 挂的是 `Bone_Wp00_11`，而 `Bone_Wp00_*` 是**挂在右手上的武器骨链**
    #   （`Bip01_R_Hand → Dummy01 → Bone_Rweapon01 → Wp00_01 … _11`，静态偏移累计 79.7），
    #   卡希尔那只泰迪熊就蒙在这条链上。爱琳 `[ch03-dash]` 写的是 `WMeshIdx=-1`（不拿东西）⇒
    #   同一份特效在她身上会从**离拳头约 69 的空气里**冒出来（她骨盆到头才 24）。
    #   顶上来的是 `CH03_MutuStand-P00.efx`：**结构同级**（2 个发射器，和 `CH01_Jab00` 的
    #   9306 字节 / 2 发射器对得上；而 `CH03_DashAttack00` 是 6 个发射器的大招特效），
    #   挂 `Bip01_R_Hand`，用的是她自己的 `CH03_MutuAttack00.dds` 拳击命中贴图。
    #   ★ 证据：`CH01_Jab00.efx`(9306) 和 `CH01_DashAttack00.efx`(9314) 只差 8 字节、
    #     同一对贴图 —— 卡希尔的「普通拳」本来就是拿冲刺特效改的，不是独立一份。
    "CH01/WP00/Efx/CH01_Jab00.efx": "CH03/WP00/Efx/CH03_MutuStand-P00.efx",
    # 表情特效：原版同样发了 CH03 版（`Effects/Emotion/`），顺手一起改指
    "Emotion/Efx/CH01_Angry00.efx": "Emotion/Efx/CH03_Angry00.efx",
    "Emotion/Efx/CH01_Cry00.efx": "Emotion/Efx/CH03_Cry00.efx",
    "Emotion/Efx/CH01_ILoveYou00.efx": "Emotion/Efx/CH03_ILoveYou00.efx",
    "Emotion/Efx/CH01_Shit00.efx": "Emotion/Efx/CH03_Shit00.efx",
    # ★ 音效。爱琳**一个自带的武器 / 体感音都没有**（§24），这里只能挑「不像卡希尔」的顶上。
    #   3 号武器换弹（`Reload03`，用户 2026-09-16 实机：「换弹匣的音效还是卡希尔的」）：
    #   母本用 `ch01@Reload03.ogg` —— 0.58 s、质心 4342 Hz、49% 能量在 4 kHz 以上，
    #   是宝箱炮的金属搭扣声，和她那把抛药水的对不上，而且卡希尔就在旁边打。
    #   换成 `Water-Load.ogg`：**原版发了却一个地方都没引用的音**（ini / evn / efx / exe 全搜过），
    #   而且和 1 / 2 号那条线一致 —— 3 号的发射音用的也是没人用的 `weapon@watergun.ogg`。
    #   0.82 s、质心 2016 Hz、高频只占 15%，是一声低而柔的「灌进去」，正对 포션（药水）。
    #   ⚠ `Water-Fire` / `Water-Spawn` **有人用**（weapon.ini / exe），别顺手拿；只有 Load 和 Hit 是闲着的。
    "ch01@Reload03.ogg": "Water-Load.ogg",
    # ★★ 嗓音 / 体感音整体换给 **ch100 앨리어스（艾丽亚丝）**（用户 2026-09-16 拍板「按结构角色逐条挑」）。
    #    为什么是她，判据是**选人语音的基频**（`Sounds/CharacterChanger/ChNNN.ogg` 是各角色念自己的名字，
    #    10 个角色测出来 146~459 Hz 各不相同 ⇒ 确实是本人的声线，不是旁白）：
    #        爱琳自己 ch003 = **324 Hz**（134 个有声帧，全表最稳的一条）
    #        卡希尔 459（全表最高，难怪一听就出戏）／泰尔 329／프로코 286
    #        **앨리어스 269**／진 242（男）／발키리 216
    #    ⇒ 在「有整套嗓音 + 体感音、且**不是基础角色**」的三个（ch100 / ch101 / ch102）里，
    #      앨리어스离爱琳的 324 最近，而且是女角。基础角色 ch00/ch02 音更近但**天天同屏**，
    #      换过去等于把「像卡希尔」换成「像泰尔」，白改（§24）。
    #    ⇒ 一个人一套声线：受击 / 吆喝 / 脚步 / 蹲行 / 拳脚全取她，别东拼西凑。
    "ch01@Damage.ogg": "ch100@Damage.ogg",                      # 大伤 + 格斗被打的闷哼 ×4
    "ch01@Voice01.ogg": "ch100@Voice01.ogg",                    # 格斗发力吆喝 ×2
    "ch01@Voice02.ogg": "ch100@Voice02.ogg",                    # 同上 ×2
    "ch01@Voice03.ogg": "ch100@Voice03.ogg",                    # 被打飞时的叫声
    "ch01@MutuDamaged.ogg": "ch100@MutuDamaged.ogg",            # 格斗受击
    "ch01@MutuDamagedFly.ogg": "ch100@MutuDamagedFly.ogg",      # 格斗被打飞
    "ch01@Mutu-GuardDamaged.ogg": "ch100@Mutu-GuardDamaged.ogg",  # 格挡住的闷响
    "ch01@Run-L.ogg": "ch100@Run-L.ogg",                        # 跑步脚步（左）×3
    "ch01@Run-R.ogg": "ch100@Run-R.ogg",                        # 跑步脚步（右）×3
    "ch01@Crouch-L.ogg": "ch100@Crouch-L.ogg",                  # 蹲行（左）
    "ch01@Crouch-R.ogg": "ch100@Crouch-R.ogg",                  # 蹲行（右）
    "ch01@jab.ogg": "ch100@jab.ogg",                            # 普通近身一击
    "ch01@MutuStand-P02.ogg": "ch100@MutuStand-P02.ogg",        # 格斗拳脚命中（母本复用在 5 个动作上）
    "ch01@MutuStand-K00-1.ogg": "ch100@MutuStand-K00-1.ogg",    # 强踢
    "ch01@Dash.ogg": "ch100@dash.ogg",                          # 冲刺（★ 盘上是小写 dash，evn 里写的是大写）
    "ch01@dash_.ogg": "ch100@dash_.ogg",                        # 冲刺起手
    "ch01@Martial03.ogg": "ch100@Martial03.ogg",                # 3 号武器冲刺的武打音
}

#: 母本里**没有对等件可换**的音，故意留着卡希尔的 —— 全游戏只有 ch01 有这些文件。
#: 记在这里是为了让下一个会话别再查一遍（都是打击 / 风声层，不是嗓音）：
#:   `MutuStand-P01-2/3/4`（蹲攻的叠加打击层）、`MutuJump-P01`（跳攻）、`Martial04`（Dash02 武打音）、
#:   `Dash03_01/02/03_frame_*`（3 号武器冲刺的三段风声）
#: 另有三个**永远不会响**的：`dash05` / `Dash04_01` / `Dash-B04_01` —— 那是 4 / 5 号武器的冲刺，
#: 爱琳的 `weapon.ini` 只定义了 01/02/03。
UNMATCHED = (
    "ch01@MutuStand-P01-2.ogg", "ch01@MutuStand-P01-3.ogg", "ch01@MutuStand-P01-4.ogg",
    "ch01@MutuStand-P03.ogg", "ch01@MutuJump-P01.ogg", "ch01@Martial04.ogg",
    "ch01@Dash03_01_frame_07.ogg", "ch01@Dash03_02_frame_11.ogg", "ch01@Dash03_03_frame_16.ogg",
    "ch01@dash05.ogg", "ch01@Dash04_01_frame_01.ogg", "ch01@Dash-B04_01_frame_01.ogg",
)

#: 同上，但这一张是**特效**。`audit_leftover_ch01("efx")` 每次跑都和它对账
#: —— 音效那边一直有守卫，特效这边 2026-09-17 之前没有。
#:
#: ★ 这 6 条**一条都放不出来**，所以留着卡希尔的没有代价：它们全挂在
#: `Dash01/02/03/05` / `Dash-B04` 上，而爱琳在 `ChrProps.ini` 里**只有 `Dash00-*` 一套参数**
#: （16 个角色里 13 个都只有 Dash00），冲刺攻击 `[ch03-dash]` 又写死 `WAnimIdx=0`
#: ⇒ 她永远只放 `Dash00`。真正会放出来的那一条是 `Jab00`，已经改指她自己的了（见 EVN_RES_REMAP）。
UNMATCHED_EFX = (
    "CH01/WP00/Efx/CH01_Dash-B04.efx", "CH01/WP00/Efx/CH01_Dash04.efx",
    "CH01/WP00/Efx/CH01_DashAttack01.efx", "CH01/WP00/Efx/CH01_DashAttack02.efx",
    "CH01/WP00/Efx/CH01_DashAttack03.efx", "CH01/WP00/Efx/CH01_DashAttack05.efx",
)


# ---------------------------------------------------------------------------
# 四元数重采样
# ---------------------------------------------------------------------------

def _rot_at(keys, t):
    """旋转关键帧 (n,5)=(t,x,y,z,w) 在 t 的值：相邻两键做符号对齐后的归一化线性插值。"""
    times = keys[:, 0]
    if t <= times[0] or len(keys) == 1:
        return keys[0, 1:].copy()
    if t >= times[-1]:
        return keys[-1, 1:].copy()
    i = int(np.searchsorted(times, t, side="right") - 1)
    q0, q1 = keys[i, 1:], keys[i + 1, 1:].copy()
    if q0 @ q1 < 0:
        q1 = -q1
    a = (t - times[i]) / (times[i + 1] - times[i])
    q = q0 * (1 - a) + q1 * a
    return q / max(np.linalg.norm(q), 1e-9)


def _lin_at(keys, t):
    return mtntool._key_at(keys, t)


def resample_rot(src_keys, src_dur, dst_dur):
    """把源旋转轨道按 src_dur -> dst_dur 拉伸后，在爱琳的每一帧（120 tick）上重采样。"""
    n = int(round(dst_dur * 3600.0 / TPF))
    out = np.zeros((n + 1, 5))
    for k in range(n + 1):
        t = k * TPF
        out[k, 0] = t
        out[k, 1:] = _rot_at(src_keys, t * (src_dur / dst_dur) if dst_dur > 0 else 0.0)
    return out


def fit(keys, dur_ticks, period_ticks):
    """把爱琳自己的轨道装进新时长：长了就截（末尾补一个插值键），短了就按原时长循环铺满
    （Stand / Idle 都是首尾相接的循环）。至少保留一键。"""
    keep = keys[keys[:, 0] <= dur_ticks + 1e-6]
    if len(keep) == 0:
        keep = keys[:1]
    if keys[-1, 0] > dur_ticks + 1e-6:
        if keep[-1, 0] < dur_ticks - 1e-6:
            keep = np.vstack([keep, np.concatenate([[dur_ticks], _lin_at(keys, dur_ticks)])])
        return keep
    if len(keys) > 1 and period_ticks > 0 and keys[-1, 0] < dur_ticks - 1e-6:
        out = [keys]
        t0 = period_ticks
        while t0 < dur_ticks - 1e-6:
            k = keys[1:].copy() if abs(keys[0, 0]) < 1e-6 else keys.copy()
            k[:, 0] += t0
            out.append(k[k[:, 0] <= dur_ticks + 1e-6])
            t0 += period_ticks
        keep = np.vstack(out)
    return keep


# ---------------------------------------------------------------------------
# 移植
# ---------------------------------------------------------------------------

def retarget(dst, src, dur_from, take=take_upper):
    """dst：爱琳（母本 ch01）的同名动作；src：ch102 的源动作；take：哪些骨抄源的旋转。

    返回新 Mtn（树 = dst 的树）。
    """
    m = mtntool.Mtn()
    m.path = dst.path
    m.set_name = dst.set_name
    m.ticks_per_sec = dst.ticks_per_sec
    m.duration = src.duration if dur_from == "src" else dst.duration
    m.nodes = [(n, p, mat.copy()) for n, p, mat in dst.nodes]
    m.tail = b""          # 母本 Crouch-Start 之外没有残片；移植的 5 个动作都不是它
    dur_ticks = m.duration * m.ticks_per_sec
    period = dst.duration * dst.ticks_per_sec
    m.track_order = list(dst.track_order)
    static_pos = {n: mat[3, :3].copy() for n, _, mat in dst.nodes}
    moved = []
    for name in dst.track_order:
        rot, pos, scl = (k.copy() for k in dst.tracks[name])
        if take(name) and name in src.tracks:
            rot = resample_rot(src.tracks[name][0], src.duration, m.duration)
            moved.append(name)
        else:
            rot = fit(rot, dur_ticks, period)
        m.tracks[name] = (rot, fit(pos, dur_ticks, period), fit(scl, dur_ticks, period))
    # 选中的骨里源有轨道、爱琳没有的（理论上没有，兜底）：旋转抄源，平移用树里的静态位置
    for name in src.track_order:
        if take(name) and name not in m.tracks and name in static_pos:
            rot = resample_rot(src.tracks[name][0], src.duration, m.duration)
            pos = np.array([[0.0, *static_pos[name]]])
            scl = np.array([[0.0, 1.0, 1.0, 1.0]])
            m.tracks[name] = (rot, pos, scl)
            m.track_order.append(name)
            moved.append(name)
    return m, moved


def shift_cyclic(m, off_ticks):
    """把一条**循环**动作的所有旋转轨道在时间上整体平移 `off_ticks`（首尾相接，取模）。

    只动旋转 —— 平移 / 缩放本来就不抄（§20）。返回新的 Mtn（源只读）。
    """
    out = mtntool.Mtn()
    for k in mtntool.Mtn.__slots__:
        setattr(out, k, getattr(m, k))
    T = m.duration * m.ticks_per_sec
    out.tracks = {}
    for name, (rot, pos, scl) in m.tracks.items():
        if len(rot) > 1 and abs(off_ticks) > 1e-9:
            r = rot.copy()
            for i in range(len(r)):
                r[i, 1:] = _rot_at(rot, (r[i, 0] - off_ticks) % T)
            rot = r
        out.tracks[name] = (rot, pos, scl)
    return out


def _fwd_signal(m, bone, n):
    """一个周期里 `bone` 相对骨盆的**前后**位移（角色脸朝 -z ⇒ 取 -z），去均值归一化。"""
    T = m.duration * m.ticks_per_sec
    v = np.array([(lambda W: W["Bip01_Pelvis"][3, 2] - W[bone][3, 2])(mtntool.world_mats(m, T * k / n))
                  for k in range(n)])
    v -= v.mean()
    return v / max(np.linalg.norm(v), 1e-9)


def leg_sync_phase(dst, src, take, side="L"):
    """返回让移植后「同侧手 ↔ 脚」最反相的源时间平移（落在动作自己的帧格上）。

    **为什么需要**：两套跑步循环的起点不一样 —— 卡希尔的腿相位和 ch00 差约 1/8 个周期。
    直接把 ch00 的摆臂抄过来，手会和爱琳（= 卡希尔）的腿差半拍，看着就是「不协调」。

    **判据是算出来的，不是写死的常数**（铁律 10）：在动作自己的帧格上把一个周期试一遍，
    取「同侧手前后位移 · 同侧脚前后位移」最负的那个 —— 真人跑步就是手脚反相。
    换一份源动作、或者母本动作改了，这个值自己会重新算。
    """
    T = src.duration * src.ticks_per_sec
    n = max(4, int(round(dst.duration * dst.ticks_per_sec / TPF)))
    leg = _fwd_signal(dst, "Bip01_%s_Foot" % side, n)
    best = (0.0, 1.0)
    for k in range(n):
        off = T * k / n
        m, _ = retarget(dst, shift_cyclic(src, off), "dst", take)
        c = float(_fwd_signal(m, "Bip01_%s_Hand" % side, n) @ leg)
        if c < best[1]:
            best = (off, c)
    return best


def drop_tracks(m, names):
    """删掉这些骨的轨道 ⇒ 引擎用树里的静态矩阵（母本里本来就有 103 根骨没有轨道）。"""
    dropped = []
    for name in names:
        if name in m.tracks:
            del m.tracks[name]
            m.track_order.remove(name)
            dropped.append(name)
    return dropped


def patch_static(m, patches):
    """把静态骨的局部矩阵整块换掉。带轨道的先把轨道删掉（卡希尔的 Attack03 / Idle103 让炮管
    `Bone_Wp03_BarrelPoint` 后坐，那是宝箱炮的动作，手杖用不上，而且它会把枪口点一起带跑）。"""
    changed = drop_tracks(m, list(patches))
    for name, M in patches.items():
        i = m.names.index(name)
        n, p, mat = m.nodes[i]
        M32 = np.asarray(M, dtype="<f4").astype(np.float64)
        if not np.array_equal(mat, M32):
            m.nodes[i] = (n, p, M32)
            if name not in changed:
                changed.append(name)
    return changed


def audit_leftover_ch01(kind="sound"):
    """扫 ch03 落盘后的 `.evn`，列出还指着卡希尔、**而且文件真的存在**的资源。

    `kind="sound"` 查音（相对 `Sounds/`），`kind="efx"` 查特效（相对 `Effects/`）——
    `.evn` 里两类走的是同一个 `<ResourcePath>` 标签，靠**文件落在哪个盘上**区分。

    死引用不算（`FX/char/KR/voice/ch01_Casil/…` 那批原版就没发过，本来就不响，§4）。
    返回排序后的列表，给 main() 和 `UNMATCHED` / `UNMATCHED_EFX` 对账。
    """
    import re
    base = SOUNDS if kind == "sound" else EFFECTS
    left = set()
    for p in glob.glob(os.path.join(CH03, "*.evn")):
        text = open(p, "rb").read().decode("cp949", "replace")
        for res in re.findall(r"<ResourcePath>([^<]+)</ResourcePath>", text):
            if not re.search(r"(?i)ch01", res):
                continue
            if os.path.exists(os.path.join(base, res.replace("/", os.sep))):
                left.add(res)
    return sorted(left)


def _write_if_changed(path, blob):
    if os.path.exists(path) and open(path, "rb").read() == blob:
        return False
    with open(path, "wb") as f:
        f.write(blob)
    return True


# ---------------------------------------------------------------------------
# 量给 weapons.py 用的数字：移植后的 Stand01 里，双手 / 武器骨 / 枪口点在 Bone_Wp01_R02 局部坐标里的位置
# ---------------------------------------------------------------------------

def rig_report(m, t=0.0, bone="Bone_Wp01_R02"):
    W = mtntool.world_mats(m, t)
    inv = np.linalg.inv(W[bone])
    out = {}
    for b in ("Bip01_R_Hand", "Bip01_L_Hand", "Bip01_L_Finger1", "Bip01_R_Finger1", "Bip01_Head", "Bip01_Neck",
              "Bip01_R_Forearm", "Bip01_L_Forearm", "Bone_Wp01_R_FirePoint", "Bone_Wp01_R_BarrelPoint", "Bone_Wp01_R03"):
        p = np.array([*W[b][3, :3], 1.0]) @ inv
        out[b] = (p[:3], W[b][3, :3])
    return out, W


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--report", action="store_true", help="打印移植后 Stand01 / Attack01 里手和枪口点在 R02 局部坐标里的位置")
    args = ap.parse_args(argv)

    import rig
    STATIC_PATCHES.update(rig.all_static_patches())

    files = sorted(glob.glob(os.path.join(CH01, "*.mtn")))
    assert len(files) == 86, len(files)
    n_written = 0
    built = {}
    for f in files:
        base = os.path.basename(f)
        clip = base.split("@", 1)[1][:-4]
        out_name = base.replace("Ch01", "ch03").replace("ch01", "ch03")
        dst = mtntool.parse(f)
        moved, note = [], ""
        if clip in CLIPS:
            spec = CLIPS[clip]
            src = src_mtn(spec["src"], spec["clip"])
            note = ""
            if spec.get("sync_legs"):
                off, c = leg_sync_phase(dst, src, spec["take"])
                src = shift_cyclic(src, off)
                note = "  摆臂相位 %+.2f 帧（手·脚 %+.2f）" % (off / TPF, c)
            dst, moved = retarget(dst, src, spec["dur"], spec["take"])
            # 卡希尔在 Reload01 / Idle101 里给手枪骨 R02 加了翻转轨道（换弹甩枪）；步枪刚性绑在 R02 上，留着会在手里乱转
            drop_tracks(dst, ["Bone_Wp01_R02", "Bone_Wp01_L02"])
        changed = patch_static(dst, STATIC_PATCHES)
        blob = mtntool.write(dst)
        chk = mtntool.parse_bytes(blob)
        assert mtntool.write(chk) == blob
        assert [(n, p) for n, p, _ in chk.nodes] == [(n, p) for n, p, _ in mtntool.parse(f).nodes], "骨架树变了"
        out_path = os.path.join(CH03, out_name)
        built[clip] = chk
        if clip in CLIPS:
            print("%-22s 时长 %.3fs  从 %-5s 移植 %2d 根骨%s"
                  % (out_name, chk.duration, CLIPS[clip]["src"], len(moved), note))
        if not args.dry_run:
            if _write_if_changed(out_path, blob):
                n_written += 1
    # 事件文件 ①：整份换成瓦尔基里的
    for clip, src_clip in EVN_FROM_SRC.items():
        src = os.path.join(CH102, "ch102@%s.evn" % src_clip)
        dst = os.path.join(CH03, "ch03@%s.evn" % clip)
        blob = open(src, "rb").read()
        if not args.dry_run and _write_if_changed(dst, blob):
            print("ch03@%s.evn <- ch102@%s.evn" % (clip, src_clip))
    # 事件文件 ②：在母本 ch01 那份上把资源路径改指（幂等，母本只读）
    for new in EVN_RES_REMAP.values():
        assert any(os.path.exists(os.path.join(root, *new.split("/"))) for root in (EFFECTS, SOUNDS)), \
            "改指的资源在 Effects/ 和 Sounds/ 里都找不到：%s" % new
    tag = "<ResourcePath>%s</ResourcePath>"
    unused = set(EVN_RES_REMAP)
    n_evn = 0
    for path in sorted(glob.glob(os.path.join(CH01, "*.evn"))):
        base = os.path.basename(path)
        if base.split("@", 1)[1][:-4] in EVN_FROM_SRC:   # 整份换掉的那几个不要再改
            continue
        blob = open(path, "rb").read()
        # ★ 整段 `<ResourcePath>…</ResourcePath>` 匹配，不是裸串 —— 免得 `ch01@Dash.ogg`
        #   把 `ch01@Dash_voice_01.ogg` 之类的前缀顺手改掉
        hits = [old for old in EVN_RES_REMAP if (tag % old).encode("ascii") in blob]
        if not hits:
            continue
        unused -= set(hits)
        for old in hits:
            blob = blob.replace((tag % old).encode("ascii"), (tag % EVN_RES_REMAP[old]).encode("ascii"))
        out = os.path.join(CH03, base[:2] + "03" + base[4:])   # 大小写照母本（`Ch01@Dash02.evn`）
        n_evn += 1
        if not args.dry_run and _write_if_changed(out, blob):
            print("%-22s 资源改指 %d 条：%s" % (os.path.basename(out), len(hits),
                                               ", ".join(EVN_RES_REMAP[h] for h in hits)))
    assert not unused, "EVN_RES_REMAP 里这几条在母本里根本没出现，名单该清了：%s" % sorted(unused)
    print("事件文件：%d 个 `.evn` 改了指向（%d 条映射全部命中）" % (n_evn, len(EVN_RES_REMAP)))
    left = audit_leftover_ch01()
    assert left == sorted(UNMATCHED), (
        "ch03 的 .evn 里剩下的卡希尔音和 UNMATCHED 对不上，名单该更新了：\n  多出来 %s\n  少了 %s"
        % (sorted(set(left) - set(UNMATCHED)), sorted(set(UNMATCHED) - set(left))))
    print("           剩 %d 个卡希尔的音没换（全游戏只有 ch01 有对等件，见 UNMATCHED）" % len(left))
    left_efx = audit_leftover_ch01("efx")
    assert left_efx == sorted(UNMATCHED_EFX), (
        "ch03 的 .evn 里剩下的卡希尔特效和 UNMATCHED_EFX 对不上，名单该更新了：\n  多出来 %s\n  少了 %s"
        % (sorted(set(left_efx) - set(UNMATCHED_EFX)), sorted(set(UNMATCHED_EFX) - set(left_efx))))
    print("           剩 %d 个卡希尔的特效没换（全挂在她放不出来的 Dash01/02/03/05 上，见 UNMATCHED_EFX）"
          % len(left_efx))
    print("写出 %d 个文件%s" % (n_written, "（dry-run）" if args.dry_run else ""))

    # 自检：打完补丁的树里，FirePoint 在参考姿势下必须正好落在设计的枪口点上
    np.set_printoptions(precision=2, suppress=True)
    for spec in (rig.RIFLE, rig.STAFF):
        W = mtntool.world_mats(built[spec["clip"]], spec["t"])
        got = W[spec["chain"][-1]][3, :3]
        want = rig.world_of(spec, (spec["muzzle_a"], 0, 0))
        err = float(np.linalg.norm(got - want))
        print("%-22s 枪口 %s ↔ 设计 %s  误差 %.3f" % (spec["chain"][-1], got, want, err))
        assert err < 0.05, "枪口点没落到设计位置"

    if args.report:
        np.set_printoptions(precision=2, suppress=True)
        for clip, t in (("Stand01", 0.0), ("Attack01", 0.0), ("Reload01", 12 * TPF), ("Run-F01", 6 * TPF)):
            rep, W = rig_report(built[clip], t)
            print("== %s t=%g（R02 局部 | 世界）" % (clip, t))
            for b, (loc, wp) in rep.items():
                print("   %-24s local=%s   world=%s" % (b, loc, wp))
    if not args.no_render:
        render(built)
    return 0


def render(built):
    import geometry
    import mshtool
    from PIL import Image, ImageDraw
    size = 360
    body = geometry.part_meshes(["ch0300000", "ch03H0000", "ch03B0000", "ch03G0000", "ch03L0000", "ch03S0000", "ch03W0001"])
    bbox = (np.array([-17.0, -0.5, -17.0]), np.array([17.0, 40.0, 17.0]))
    frames = [("Stand01", 0), ("Stand01", 25), ("Attack01", 1), ("Attack01", 3), ("Reload01", 12), ("Reload01", 26),
              ("Run-F01", 6), ("Run-F01", 16), ("Run-B01", 6)]
    cols = 3
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGB", (size * 2 * cols, size * rows), (30, 30, 34))
    for i, (clip, fr) in enumerate(frames):
        mtn = built[clip]
        t = min(fr * TPF, mtn.duration * mtn.ticks_per_sec)
        pp = geometry.posed(body, mtn, t)
        for j, view in enumerate(("front", "side")):
            im = Image.fromarray(mshtool.rasterize(pp, view=view, size=size, bbox=bbox))
            if j == 0:
                ImageDraw.Draw(im).text((6, 6), "ch03 %s f%d" % (clip, fr), fill=(240, 240, 240))
            sheet.paste(im, (((i % cols) * 2 + j) * size, (i // cols) * size))
    out = os.path.join(HERE, "compare_F_anim.png")
    sheet.save(out)
    print("->", out)
    render_dash(built, geometry, mshtool, Image, ImageDraw)
    render_run(built, geometry, mshtool, Image, ImageDraw)


def render_dash(built, geometry, mshtool, Image, ImageDraw):
    """冲刺攻击三行对照：卡希尔（抱着熊）/ 瓦尔基里（源）/ 爱琳（移植后）。"""
    size = 230
    n = 7
    bbox = (np.array([-28.0, -2.0, -28.0]), np.array([28.0, 50.0, 28.0]))
    rows = []
    for label, mtn, parts in (
            ("ch01@Dash00  Casil (hugging ch01W0000 = the teddy bear)",
             mtntool.parse(os.path.join(CH01, "ch01@Dash00.mtn")),
             geometry.part_meshes(["ch01W0000"], root=CH01) + geometry.part_meshes(
                 ["ch0100000", "ch01H0000", "ch01B0000", "ch01G0000", "ch01L0000", "ch01S0000"], root=CH01)),
            ("ch102@Dash00  Valkyrie (source, 0.833s)",
             mtntool.parse(os.path.join(CH102, "ch102@Dash00.mtn")),
             # 瓦尔基里是连体装：整套只有 0000 / H0000 / B0000 三件，没有独立的手套 / 下装 / 鞋
             geometry.part_meshes(["ch10200000", "ch102H0000", "ch102B0000"], root=CH102)),
            ("ch03@Dash00  Irene (retargeted, 1.0s, empty-handed like [ch03-dash] WMeshIdx=-1)",
             built["Dash00"],
             geometry.part_meshes(["ch0300000", "ch03H0000", "ch03B0000", "ch03G0000",
                                   "ch03L0000", "ch03S0000"]))):
        total = mtn.duration * mtn.ticks_per_sec
        row = Image.new("RGB", (size * n, size + 16), (30, 30, 34))
        for i in range(n):
            t = total * i / (n - 1)
            row.paste(Image.fromarray(mshtool.rasterize(geometry.posed(parts, mtn, t),
                                                        view="side", size=size, bbox=bbox)), (i * size, 16))
        ImageDraw.Draw(row).text((3, 3), label, fill=(255, 230, 0))
        rows.append(row)
    sheet = Image.new("RGB", (max(r.width for r in rows), sum(r.height for r in rows)), (30, 30, 34))
    y = 0
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height
    out = os.path.join(HERE, "compare_G_dash.png")
    sheet.save(out)
    print("->", out)


def render_run(built, geometry, mshtool, Image, ImageDraw):
    """空手跑对照：母本卡希尔（单手托熊跑）/ ch00 泰尔（源）/ 爱琳（移植后）。侧视看摆臂。"""
    size = 215
    n = 8
    bbox = (np.array([-16.0, -0.5, -16.0]), np.array([16.0, 34.0, 16.0]))
    body = ["ch03H0000", "ch03B0000", "ch03G0000", "ch03L0000", "ch03S0000"]
    rows = []
    for label, mtn, parts in (
            ("ch01@Run-F00  Casil (arm swing L/R 58.5/49.0, forearm 34.3/19.3 -- carrying the bear)",
             mtntool.parse(os.path.join(CH01, "ch01@Run-F00.mtn")),
             geometry.part_meshes(body)),
            ("ch00@Run-F00  Tayr (source; arm-vs-leg anti-phase -0.81, same DisplayHeight 75)",
             src_mtn("ch00", "Run-F00"),
             geometry.part_meshes(["ch00H0000", "ch00B0000", "ch00G0000", "ch00L0000", "ch00S0000"],
                                  root=os.path.join(CHARS, "ch00"))),
            ("ch03@Run-F00  Irene (upper body retargeted, legs unchanged)",
             built["Run-F00"], geometry.part_meshes(body))):
        total = mtn.duration * mtn.ticks_per_sec
        row = Image.new("RGB", (size * n, size + 16), (30, 30, 34))
        for i in range(n):
            row.paste(Image.fromarray(mshtool.rasterize(geometry.posed(parts, mtn, total * i / n),
                                                        view="side", size=size, bbox=bbox)), (i * size, 16))
        ImageDraw.Draw(row).text((3, 3), label, fill=(255, 230, 0))
        rows.append(row)
    sheet = Image.new("RGB", (max(r.width for r in rows), sum(r.height for r in rows)), (30, 30, 34))
    y = 0
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height
    out = os.path.join(HERE, "compare_H_run.png")
    sheet.save(out)
    print("->", out)


if __name__ == "__main__":
    # ★ 把 stdout / stderr 钉成 utf-8。调用方一**捕获**输出（管道 / 赋值给变量），
    #   CPython 就发现 stdout 不是控制台、改用 `GetACP()` = cp936 —— 中文按 GBK 落进
    #   管道而上游按 utf-8 解（满屏乱码），`✓` 这种 cp936 编不出来的字符更是直接
    #   `UnicodeEncodeError` 把进程带崩。完整来龙去脉见 `tools/pkn.py` 的 main()。
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
