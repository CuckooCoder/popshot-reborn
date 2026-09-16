#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""anim.py —— 爱琳 1 号武器（트릭스터 步枪）的持枪 / 开枪 / 换弹 / 跑动动作：从瓦尔基里（ch102）移植上半身。

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

| 骨 | 旋转 | 平移 / 缩放 |
|---|---|---|
| 上半身 `UPPER`（Spine1 以上 + 双臂 + 手指） | ch102 同名动作，按时长重采样到爱琳的帧 | 爱琳自己的（母本 ch01 同名动作） |
| 其余（根骨 / 骨盆 / 双腿 / 头发 / 裙摆 / 尾巴 / 武器件骨） | 爱琳自己的 | 爱琳自己的 |

时长：Stand01 / Reload01 两边本来就相等；Run-F01 / Run-B01 取爱琳的 0.667 s（跑步循环速度和她的
`ChrSpeed` 是配套的，ch102 的 0.8 s 上半身重采样过来）；Attack01 取 ch102 的 0.1 s（步枪后坐，
对应 `CoolingTime=90`；卡希尔那个 0.667 s 是双枪轮射，留着会让每一枪都做半套挥枪）。

## 静态骨补丁（`STATIC_PATCHES`）

武器骨链全是静态骨（没有轨道，位置写死在树里，86 个文件一样）。引擎按名字找
`Bone_wp01_firepoint`、找不到才找 `Bone_wp01_R_firepoint`（`0x506b74` / `0x506ba7`），
左手那个 `_L_FirePoint` 根本不用。步枪的枪口在哪，`Bone_Wp01_R_FirePoint` 就得挪到哪，
否则枪火 / 弹道线从空气里冒出来。补丁对 86 个文件一起打，树保持一致。
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
TPF = mtntool.TICKS_PER_FRAME

# 爱琳动作名 -> (ch102 源动作, 时长取谁)
CLIPS = {
    "Stand01": ("Stand01", "dst"),
    "Attack01": ("Attack01", "src"),
    "Reload01": ("Reload01", "dst"),
    "Run-F01": ("Run-F01", "dst"),
    "Run-B01": ("Run-B01", "dst"),
    "Idle101": ("Idle101", "src"),     # 持枪发呆：ch102 2.667 s，爱琳自己的下半身 / 头发轨道按 1.667 s 循环铺满
}

UPPER = {"Bip01_Spine1", "Bip01_Spine2", "Bip01_Neck", "Bip01_Head", "Bip01_HeadNub"}
for side in "LR":
    for b in ("Clavicle", "UpperArm", "Forearm", "Hand", "Finger0", "Finger01", "Finger0Nub",
              "Finger1", "Finger11", "Finger1Nub", "Finger2", "Finger21", "Finger2Nub"):
        UPPER.add("Bip01_%s_%s" % (side, b))

# 静态骨补丁：骨名 -> 新的 4x4 局部矩阵。由 rig.py 从步枪 / 手杖的设计框架算出（BarrelPoint / _03 / FirePoint 三根一链），
# 在 main() 里懒加载（rig 反过来要用本文件的 retarget，避免循环导入）。
STATIC_PATCHES = {}

# 事件文件：换弹动作换成瓦尔基里的（她那份只在第 21 帧响一次拉栓声，卡希尔的是双枪两次上膛）
EVN_FROM_SRC = {"Reload01": "Reload01"}


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

def retarget(dst, src, dur_from):
    """dst：爱琳（母本 ch01）的同名动作；src：ch102 的源动作。返回新 Mtn（树 = dst 的树）。"""
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
        if name in UPPER and name in src.tracks:
            rot = resample_rot(src.tracks[name][0], src.duration, m.duration)
            moved.append(name)
        else:
            rot = fit(rot, dur_ticks, period)
        m.tracks[name] = (rot, fit(pos, dur_ticks, period), fit(scl, dur_ticks, period))
    # 上半身里源有轨道、爱琳没有的骨（理论上没有，兜底）：旋转抄源，平移用树里的静态位置
    for name in src.track_order:
        if name in UPPER and name not in m.tracks and name in static_pos:
            rot = resample_rot(src.tracks[name][0], src.duration, m.duration)
            pos = np.array([[0.0, *static_pos[name]]])
            scl = np.array([[0.0, 1.0, 1.0, 1.0]])
            m.tracks[name] = (rot, pos, scl)
            m.track_order.append(name)
            moved.append(name)
    return m, moved


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
        moved = []
        if clip in CLIPS:
            src_clip, dur_from = CLIPS[clip]
            src = mtntool.parse(os.path.join(CH102, "ch102@%s.mtn" % src_clip))
            dst, moved = retarget(dst, src, dur_from)
            # 卡希尔在 Reload01 / Idle101 里给手枪骨 R02 加了翻转轨道（换弹甩枪）；步枪刚性绑在 R02 上，留着会在手里乱转
            drop_tracks(dst, ["Bone_Wp01_R02", "Bone_Wp01_L02"])
        changed = patch_static(dst, STATIC_PATCHES)
        blob = mtntool.write(dst)
        chk = mtntool.parse_bytes(blob)
        assert mtntool.write(chk) == blob
        assert [(n, p) for n, p, _ in chk.nodes] == [(n, p) for n, p, _ in mtntool.parse(f).nodes], "骨架树变了"
        out_path = os.path.join(CH03, out_name)
        built[clip] = chk
        if clip in CLIPS or changed:
            print("%-22s 时长 %.3fs  移植 %2d 根上半身骨  静态补丁 %s" % (out_name, chk.duration, len(moved), changed or "-"))
        if not args.dry_run:
            if _write_if_changed(out_path, blob):
                n_written += 1
    # 事件文件
    for clip, src_clip in EVN_FROM_SRC.items():
        src = os.path.join(CH102, "ch102@%s.evn" % src_clip)
        dst = os.path.join(CH03, "ch03@%s.evn" % clip)
        blob = open(src, "rb").read()
        if not args.dry_run and _write_if_changed(dst, blob):
            print("ch03@%s.evn <- ch102@%s.evn" % (clip, src_clip))
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


if __name__ == "__main__":
    sys.exit(main())
