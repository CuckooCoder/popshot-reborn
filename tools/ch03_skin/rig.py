#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rig.py —— 步枪 / 手杖的「设计框架」：在参考姿势的**世界坐标**里定义，换算成武器骨的局部坐标。

anim.py（枪口点骨链补丁）和 weapons.py（网格摆放）共用这一份数字，两边不会各说各话。

## 为什么在世界坐标里定义

武器网格刚性绑在一根手骨的子骨上（步枪 `Bone_Wp01_R02`，手杖 `Bone_Wp03_01`），
在哪个姿势下量都一样。但「枪身轴线在右手上方 4 个单位、枪口在左手指尖前 10 个单位」这种话
只有在**一个具体姿势的世界坐标**里才说得清楚 ⇒ 参考姿势固定为：

- 步枪：**移植后的** `Stand01` 第 0 帧（anim.retarget 出来的那份，不是母本的）
- 手杖：`Stand03` 第 0 帧（卡希尔的持箱姿势，没改）

`frame_local()` 把世界坐标的 (origin, f, u) 乘 `inv(骨的世界矩阵)` 变成骨骼局部坐标，
weapons.py 的 `Part.place()` 直接吃。`static_patches()` 用同一套框架算出
`*_BarrelPoint / *_03 / *_FirePoint` 三根静态骨的新局部矩阵，anim.py 打进全部 86 个 `.mtn`。

## 数字从哪来（2026-09-16 实测，见 FINDINGS §19）

移植后 Stand01 第 0 帧：右手 (0.55, 64.3, -15.7)，右手食指根 (5.9, 73.3, -18.9)（指尖朝上），
左手 (3.9, 61.3, -25.5)，左手食指根 (0.1, 61.6, -35.7)（指尖朝前，掌心朝上），左前臂水平在 y≈61.5。
瓦尔基里自己的步枪相对她双手的关系：轴线在双手上方 ≈10、偏左 6~12（枪架在左前臂上，右手扶着枪身右侧）。
按爱琳的身高（75 / 90）缩一下 ⇒ 轴线过 (9.5, 68.5, z)，右手指尖刚好搭在枪身右上沿。
"""
from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
sys.path.insert(0, HERE)
import mtntool  # noqa: E402

CHARS = os.path.join(os.path.dirname(TOOLS), "game_patched", "Pack_develop", "Models", "Characters")
CH01 = os.path.join(CHARS, "ch01")


def _norm(v):
    v = np.asarray(v, dtype=np.float64)
    return v / max(np.linalg.norm(v), 1e-12)


# 设计坐标 (a, b, c) = a·f + b·u + c·s + origin，s = f × u。角色脸朝 -z、右手在 -x 侧 ⇒ s = +x = 角色的左边。
RIFLE = dict(
    clip="Stand01", t=0.0, bone="Bone_Wp01_R02",
    origin=(9.5, 68.5, -17.0),          # 枪身轴线上、右手正上方的那个点 = 设计原点
    f=(0.0, 0.0, -1.0), u=(0.0, 1.0, 0.0),
    barrel_a=16.0, muzzle_a=38.3,      # BarrelPoint / FirePoint 在轴线上的位置（方口前脸在 a=38.2）
    chain=("Bone_Wp01_R_BarrelPoint", "Bone_Wp01_R03", "Bone_Wp01_R_FirePoint"),
)

STAFF = dict(
    clip="Stand03", t=0.0, bone="Bone_Wp03_01",
    origin=(-13.48, 54.92, -19.78),     # 右手掌心 = Bone_Wp03_01 原点（§18），杖身穿过这里
    f=(0.0, 0.0, -1.0), u=(0.0, 1.0, 0.0),
    barrel_a=30.0, muzzle_a=50.0,      # 花蕊那根长尖的尖端 = 枪口
    chain=("Bone_Wp03_BarrelPoint", "Bone_Wp03_03", "Bone_Wp03_FirePoint"),
)


_POSE_CACHE = {}


def reference_pose(clip):
    """参考姿势的 Mtn：移植集里的动作用 anim.retarget 现算，其余直接读母本 ch01。"""
    if clip in _POSE_CACHE:
        return _POSE_CACHE[clip]
    import anim
    dst = mtntool.parse(os.path.join(CH01, "ch01@%s.mtn" % clip))
    if clip in anim.CLIPS:
        spec = anim.CLIPS[clip]
        # 参考姿势只用来量武器摆放，`sync_legs` 那种摆臂相位对齐和它无关（Stand01 / Stand03 都不开）
        dst, _ = anim.retarget(dst, anim.src_mtn(spec["src"], spec["clip"]), spec["dur"], spec["take"])
    _POSE_CACHE[clip] = dst
    return dst


def world_frame(spec):
    """设计框架的世界矩阵（行 = f, u, s, origin）。"""
    f = _norm(spec["f"])
    u = np.asarray(spec["u"], dtype=np.float64)
    u = _norm(u - (u @ f) * f)
    s = np.cross(f, u)
    M = np.eye(4)
    M[0, :3], M[1, :3], M[2, :3], M[3, :3] = f, u, s, spec["origin"]
    return M


def frame_local(spec):
    """(origin, f, u) 换算到 spec['bone'] 的局部坐标（场景单位），给 Part.place() 用。"""
    W = mtntool.world_mats(reference_pose(spec["clip"]), spec["t"])[spec["bone"]]
    inv = np.linalg.inv(W)
    G = world_frame(spec) @ inv
    return G[3, :3].copy(), _norm(G[0, :3]), _norm(G[1, :3])


def static_patches(spec):
    """chain 三根静态骨的新局部矩阵：x 轴沿枪身、依次落在 barrel_a / barrel_a+2 / muzzle_a。"""
    mtn = reference_pose(spec["clip"])
    W = mtntool.world_mats(mtn, spec["t"])
    F = world_frame(spec)
    out = {}
    parent_world = {}
    for name, a in zip(spec["chain"], (spec["barrel_a"], spec["barrel_a"] + 2.0, spec["muzzle_a"])):
        Wd = F.copy()
        Wd[3, :3] = F[3, :3] + a * F[0, :3]
        parent = mtn.parent_name(name)
        Wp = parent_world.get(parent, W[parent])
        out[name] = Wd @ np.linalg.inv(Wp)
        parent_world[name] = Wd
    return out


def all_static_patches():
    out = {}
    for spec in (RIFLE, STAFF):
        out.update(static_patches(spec))
    return out


def world_of(spec, design_pt):
    """设计坐标 -> 参考姿势的世界坐标（自检用）。"""
    F = world_frame(spec)
    a, b, c = design_pt
    return F[3, :3] + a * F[0, :3] + b * F[1, :3] + c * F[2, :3]


if __name__ == "__main__":
    np.set_printoptions(precision=3, suppress=True)
    for spec in (RIFLE, STAFF):
        o, f, u = frame_local(spec)
        print("%s @ %s：origin=%s f=%s u=%s" % (spec["bone"], spec["clip"], o, f, u))
        for n, M in static_patches(spec).items():
            print("   %-24s T=%s R0=%s" % (n, M[3, :3], M[0, :3]))
