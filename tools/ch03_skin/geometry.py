#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""geometry.py —— 爱琳的追加几何（X1 M7 阶段 C）：辫子 / 侧发 / 触角 / 角状发簇 / 花瓣披风 / 花瓣袖 / 花瓣裙摆。

    C:\\Python314\\python.exe tools/ch03_skin/geometry.py              # 写 ch0300000 / ch03B0000 的 .msh + .dds，并渲对照图
    C:\\Python314\\python.exe tools/ch03_skin/geometry.py --dry-run    # 只渲对照图，不写
    C:\\Python314\\python.exe tools/ch03_skin/geometry.py --only hair  # 只做头发件（或 body）

## 在重建链里的位置（★ 必须最后跑）

    mkchar.py（克隆母本）→ recolor.py（64×64 重着色，产物也存 tools/ch03_skin/out/*.png）
    → ears.py（H0000.msh 尖耳）→ **geometry.py（本脚本）** → weapons.py（武器 + 弹体，和本脚本互不依赖）

本脚本的输入是**只读母本** ch01 的 `.msh` 和 `out/*.png`（recolor 的中间产物），
不读 ch03 目录里任何东西 ⇒ 幂等；但它写出的贴图是 128×128、UV 也改了，
所以 recolor.py 之后**必须**再跑一遍本脚本，否则 64×64 贴图配 ×0.5 的 UV 会只采到左上角 1/4。

## 设计（照 refs/irene-concept.png）

| 部位 | 挂在哪根骨 | 为什么 |
|---|---|---|
| 两根及膝辫子 | `Bone_Hairtail_{L,R}01..07`，每环 100% 挂一节 | 7 节物理骨，动画里真的在甩；绑定姿态里链子向后外侧伸，游戏里自然垂下 |
| 脸侧长发（到胸以下） | `Bone_Hair_{L,R}01`（上段）/ `02`（下段） | 原版侧发就是这两节；下段刚性挂 02，随头摆 |
| 头顶两根触角发 | `Bone_RabbitEar_{L,R}01/02` | 兔耳骨在头顶，有 51 帧动画，会跟着动作晃 |
| 角状发簇 + 叶片 | `Bip01_Head` | 短、贴头，不需要物理 |
| 背后两片大花瓣 | 顶排 `Bip01_Spine2`，往下 `Bone_Wing_{L,R}01` | 翅膀骨在肩胛，动画里有 ±25° 的摆动，花瓣会随之摇 |
| 上臂花瓣 | `Bip01_{L,R}_UpperArm`（顶排掺 Clavicle） | 披在上臂外侧，抬枪时跟着手臂走 |
| 前臂喇叭花瓣袖 | `Bip01_{L,R}_Forearm` 100% | 顶环在肘关节（骨头原点，转动不变点），不会撕 |
| 8 片裙摆花瓣 | `Bone_Skirt_X01`（腰）/ `X02`（中）/ `X02+X03`（尖） | 原版裙摆骨 8 向 × 4 节，动画里飘 |

骨头的绑定位置从目标文件自己的 offset 矩阵取；目标文件里没有的骨头先找**同绑定姿态**的原版部件借
（`ch0100001/2`：Hairtail 03..06；`ch01B0001/2`：Skirt_*03；`ch01B0006`：Ribbon），
再没有的（兔耳、翅膀、链尾 07）用 Stand00 第 0 帧的局部矩阵乘父骨绑定矩阵推出来 —— 见 mtntool.py 单位说明。

## 贴图：128×128，原图放左上角、旧 UV ×0.5

只用一个材质（B 类部件原版没有双材质先例；头发件有 39 个双材质，但没必要冒险）。
128×128 R5G6B5 在 B 类里有 4 个原版先例（ch01B0049 等），H 类 13 个。
剩下三个象限画新几何用的花瓣 / 发丝 / 叶片渐变。

## 安全边界（会崩的那几条）

- 所有骨骼名都在 86 个 `.mtn` 共同的 229 节点树里（`mtntool.py check` 证过全部一致），写出前逐个断言。
- 每顶点 ≤ 3 根骨（原版最大 3），权重和 = 1。
- 面的绕向：`cross(b-a, c-a)` 指向外法线（原版 98%+ 的面如此；引擎开着背面剔除 ——
  翅膀件是封闭体、蝴蝶结件有 15 组反向重复面），所以薄片一律做成管子或双面。
"""
from __future__ import annotations

import argparse
import math
import os
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
import dds_edit  # noqa: E402
import mkchar  # noqa: E402
import mshtool  # noqa: E402
import mtntool  # noqa: E402

CHARS = os.path.join(ROOT, "game_patched", "Pack_develop", "Models", "Characters")
CH01 = os.path.join(CHARS, "ch01")
CH03 = os.path.join(CHARS, "ch03")
OUT_PNG = os.path.join(HERE, "out")
CONCEPT = os.path.join(ROOT, "develop_history", "X_自定义游戏内容Mod开发", ".claude", "refs", "irene-concept.png")
TEX = 128
SCENE_SCALE = 2.8791      # 默认部件顶点 = 场景单位 / 2.8791（.msh 0x04 处的世界矩阵）


def norm(v):
    v = np.asarray(v, dtype=np.float64)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def lerp(a, b, t):
    return np.asarray(a, dtype=np.float64) * (1 - t) + np.asarray(b, dtype=np.float64) * t


# ---------------------------------------------------------------------------
# 绑定姿态
# ---------------------------------------------------------------------------

class BindPose:
    """某个目标 `.msh` 的绑定姿态：骨骼名 -> 绑定世界矩阵（网格单位）。

    来源优先级：目标文件自己的 offset > 同绑定姿态的捐赠文件 > 由父骨 + Stand00 第 0 帧局部矩阵推导。
    捐赠文件必须在所有共同骨头上和目标逐字节一致（误差 < 1e-3），否则拒绝。
    """

    def __init__(self, target, donors, mtn):
        self.mtn = mtn
        self.names = set(mtn.names)
        self.parent = {n: mtn.nodes[p][0] if p >= 0 else None for n, p, _ in mtn.nodes}
        self.off = dict(mshtool.bone_offsets(target))
        self.source = {n: "self" for n in self.off}
        for d in donors:
            doff = mshtool.bone_offsets(d)
            common = [b for b in doff if b in self.off]
            err = max(float(np.abs(doff[b] - self.off[b]).max()) for b in common) if common else 0.0
            if err > 1e-3:
                raise SystemExit("捐赠文件 %s 的绑定姿态和目标不一致（%d 根共同骨，最大误差 %.4f）"
                                 % (os.path.basename(d.path), len(common), err))
            for b, M in doff.items():
                if b not in self.off:
                    self.off[b] = M
                    self.source[b] = os.path.basename(d.path)
        self._bind = {}

    def bind(self, name):
        if name in self._bind:
            return self._bind[name]
        if name not in self.names:
            raise KeyError("骨骼 %s 不在骨架里" % name)
        if name in self.off:
            B = np.linalg.inv(self.off[name])
        else:
            p = self.parent[name]
            if p is None:
                raise KeyError("根骨没有 offset")
            # 父矩阵的旋转行已带 0.347 的缩放，局部平移用场景单位直接乘 —— 不要再除 2.8791
            B = mtntool.local_at(self.mtn, name, 0.0) @ self.bind(p)
            self.source[name] = "derived"
        self._bind[name] = B
        return B

    def offset(self, name):
        return self.off[name] if name in self.off else np.linalg.inv(self.bind(name))

    def origin(self, name):
        return self.bind(name)[3, :3].copy()

    def axis(self, name):
        return norm(self.bind(name)[0, :3])


# ---------------------------------------------------------------------------
# 网格构造器
# ---------------------------------------------------------------------------

class Builder:
    def __init__(self, template, texture, bindpose, uv_scale=0.5):
        self.template = template
        self.texture = texture
        self.bp = bindpose
        v = template.verts
        self.pos = [tuple(p) for p in v["pos"].astype(np.float64)]
        self.nrm = [tuple(n) for n in v["nrm"].astype(np.float64)]
        self.uv = [(float(u) * uv_scale, float(w) * uv_scale) for u, w in v["uv"]]
        self.w = mshtool.vertex_weights(template)
        self.faces = [tuple(int(i) for i in f) for f in template.faces]
        self.n_old_v = len(self.pos)
        self.n_old_f = len(self.faces)
        self.bone_order = [n for n, _ in template.bones]

    # -- 基本操作 ----------------------------------------------------------
    def v(self, p, n, uv, weights):
        self.pos.append(tuple(float(x) for x in p))
        self.nrm.append(tuple(float(x) for x in norm(n)))
        self.uv.append((float(uv[0]), float(uv[1])))
        ws = [(b, float(w)) for b, w in weights if w > 1e-6]
        tot = sum(w for _, w in ws)
        self.w.append([(b, w / tot) for b, w in ws])
        for b, _ in ws:
            if b not in self.bone_order:
                if b not in self.bp.names:
                    raise SystemExit("骨骼 %s 不在骨架里！" % b)
                self.bone_order.append(b)
        return len(self.pos) - 1

    def tri(self, a, b, c):
        self.faces.append((a, b, c))

    def quad(self, a, b, c, d):
        self.tri(a, b, c)
        self.tri(a, c, d)

    def mirror_faces(self, v0, f0):
        """把 [v0:] 的顶点复制一份（法线取反），[f0:] 的面反绕向复制 —— 做双面薄片。"""
        n_new = len(self.pos) - v0
        for i in range(v0, v0 + n_new):
            self.pos.append(self.pos[i])
            self.nrm.append(tuple(-x for x in self.nrm[i]))
            self.uv.append(self.uv[i])
            self.w.append(list(self.w[i]))
        for a, b, c in list(self.faces[f0:]):
            self.faces.append((a + n_new, c + n_new, b + n_new))

    # -- 管子 ------------------------------------------------------------------
    def tube(self, centers, radii, sides, weights, uvreg, flatten=(1.0, 1.0), side_hint=None,
             twist=0.0, double=False, zigzag=None, cap_start=False):
        """沿折线 `centers` 放 `sides` 边形的环，相邻环连成管。半径 0 的环退化成一个尖点。

        每环 sides+1 个顶点（首尾重合，为了 UV 不接缝）；u 绕一圈 = uvreg 的 u0..u1，v 沿长度。
        `flatten=(fx, fz)` 把截面压成椭圆；`side_hint` 决定第一环的「宽」方向，之后沿折线平行传输，不打转。
        `twist`：每环多转的弧度（辫子用）。`zigzag=(沿轴伸出, 径向外扩)`：最后一环的偶数号顶点伸出去成锯齿边。
        """
        v0, f0 = len(self.pos), len(self.faces)
        C = np.asarray(centers, dtype=np.float64)
        n = len(C)
        T = np.zeros_like(C)
        T[0] = C[1] - C[0]
        T[-1] = C[-1] - C[-2]
        if n > 2:
            T[1:-1] = C[2:] - C[:-2]
        T = np.array([norm(t) for t in T])
        if side_hint is None:
            side_hint = (0, 1, 0) if abs(T[0][1]) < 0.9 else (1, 0, 0)
        N = norm(np.asarray(side_hint, float) - (np.asarray(side_hint, float) @ T[0]) * T[0])
        frames = []
        for i in range(n):
            N = norm(N - (N @ T[i]) * T[i])
            frames.append((N, np.cross(T[i], N)))
        seg = np.linalg.norm(np.diff(C, axis=0), axis=1)
        s = np.concatenate([[0.0], np.cumsum(seg)])
        s /= max(s[-1], 1e-9)
        u0, v0u, u1, v1 = uvreg
        fx, fz = flatten
        rings = []
        for i in range(n):
            vv = v0u + (v1 - v0u) * s[i]
            if radii[i] <= 1e-6:
                rings.append(("tip", self.v(C[i], T[i], (0.5 * (u0 + u1), vv), weights[i])))
                continue
            Ni, Bi = frames[i]
            ring = []
            for j in range(sides + 1):
                th = 2 * math.pi * j / sides + twist * i
                d = math.cos(th) * Ni * fx + math.sin(th) * Bi * fz
                p = C[i] + radii[i] * d
                if zigzag is not None and i == n - 1 and j % 2 == 0:
                    p = p + T[i] * zigzag[0] + norm(d) * zigzag[1]
                nn = norm(math.cos(th) * Ni / fx + math.sin(th) * Bi / fz)
                ring.append(self.v(p, nn, (u0 + (u1 - u0) * j / sides, vv), weights[i]))
            rings.append(("ring", ring))
        for i in range(n - 1):
            a, b = rings[i], rings[i + 1]
            if a[0] == "ring" and b[0] == "ring":
                for j in range(sides):
                    self.quad(a[1][j], a[1][j + 1], b[1][j + 1], b[1][j])
            elif a[0] == "ring" and b[0] == "tip":
                for j in range(sides):
                    self.tri(a[1][j], a[1][j + 1], b[1])
        if cap_start and rings[0][0] == "ring":
            ring = rings[0][1]
            c = self.v(C[0], -T[0], (0.5 * (u0 + u1), v0u), weights[0])
            for j in range(sides):
                self.tri(ring[j + 1], ring[j], c)
        if double:
            self.mirror_faces(v0, f0)

    # -- 花瓣 / 叶片 -------------------------------------------------------------
    def petal(self, rows, tip, uvreg, hint, bulge=0.0, double=True, edge_tilt=0.35):
        """一片从 rows[0] 垂到 tip 的花瓣。rows：[(中心, 宽方向, 半宽, 权重)]；tip：(点, 权重)。

        每排 3 个顶点（左 / 中 / 右），中点沿法线抬 `bulge` 做出弧面；`hint` 指定正面朝向，
        正面法线 = cross(宽方向, 走向)，和 hint 反了就把宽方向翻过来。默认双面。
        """
        v0, f0 = len(self.pos), len(self.faces)
        C = [np.asarray(r[0], float) for r in rows] + [np.asarray(tip[0], float)]
        u0, v0u, u1, v1 = uvreg
        um = 0.5 * (u0 + u1)
        seg = np.linalg.norm(np.diff(np.array(C), axis=0), axis=1)
        s = np.concatenate([[0.0], np.cumsum(seg)])
        s /= max(s[-1], 1e-9)
        out = []
        last_n = None
        for i, (c, side, hw, wts) in enumerate(rows):
            t = norm(C[i + 1] - C[i])
            sd = norm(np.asarray(side, float) - (np.asarray(side, float) @ t) * t)
            nrm_ = norm(np.cross(sd, t))
            if nrm_ @ np.asarray(hint, float) < 0:
                sd, nrm_ = -sd, -nrm_
            last_n = nrm_
            vv = v0u + (v1 - v0u) * s[i]
            L = self.v(C[i] - sd * hw, norm(nrm_ - edge_tilt * sd), (u0, vv), wts)
            M = self.v(C[i] + nrm_ * bulge, nrm_, (um, vv), wts)
            R = self.v(C[i] + sd * hw, norm(nrm_ + edge_tilt * sd), (u1, vv), wts)
            out.append((L, M, R))
        tp = self.v(C[-1], last_n, (um, v1), tip[1])
        for i in range(len(rows) - 1):
            (L0, M0, R0), (L1, M1, R1) = out[i], out[i + 1]
            self.quad(L0, M0, M1, L1)
            self.quad(M0, R0, R1, M1)
        L, M, R = out[-1]
        self.tri(L, M, tp)
        self.tri(M, R, tp)
        if double:
            self.mirror_faces(v0, f0)

    # -- 收尾 ----------------------------------------------------------------------
    def finish(self):
        nv = len(self.pos)
        verts = np.zeros(nv, dtype=mshtool.VERTEX_DTYPE)
        verts["pos"] = np.asarray(self.pos, dtype="<f4")
        verts["nrm"] = np.asarray(self.nrm, dtype="<f4")
        verts["uv"] = np.asarray(self.uv, dtype="<f4")
        per_bone = {}
        for i, ws in enumerate(self.w):
            if not 1 <= len(ws) <= 3:
                raise SystemExit("顶点 %d 有 %d 根骨（要求 1..3）" % (i, len(ws)))
            if abs(sum(w for _, w in ws) - 1) > 1e-4:
                raise SystemExit("顶点 %d 权重和 != 1" % i)
            for b, w in ws:
                per_bone.setdefault(b, ([], []))
                per_bone[b][0].append(i)
                per_bone[b][1].append(w)
        bones = []
        for b in self.bone_order:
            if b not in per_bone:
                continue
            if b not in self.bp.names:
                raise SystemExit("骨骼 %s 不在骨架里！" % b)
            ids, ws = per_bone[b]
            bones.append((b, np.asarray(ids, dtype="<u4"), np.asarray(ws, dtype="<f4"), self.bp.offset(b)))
        m = mshtool.assemble(self.template, verts, self.faces, self.texture, bones)
        return m


# ---------------------------------------------------------------------------
# 贴图
# ---------------------------------------------------------------------------

def region(x0, y0, x1, y1, inset=1.0):
    """像素矩形 -> UV 矩形（往里缩 inset 个纹素，躲双线性采样的串色）。"""
    return ((x0 + inset) / TEX, (y0 + inset) / TEX, (x1 - inset) / TEX, (y1 - inset) / TEX)


def _grid(w, h):
    u = (np.arange(w) + 0.5) / w
    v = (np.arange(h) + 0.5) / h
    return u[None, :], v[:, None]


def _mix(a, b, t):
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    return a * (1 - t)[..., None] + b * t[..., None]


def strand_art(w, h, base=(246, 244, 250), shade=(176, 170, 204), light=(255, 255, 255)):
    """发丝条：横向（u）两边暗中间亮，纵向（v）到梢略暗。"""
    u, v = _grid(w, h)
    across = (1 - (2 * u - 1) ** 2) ** 0.8
    col = _mix(shade, base, np.broadcast_to(across, (h, w)))
    hi = np.clip((across - 0.75) / 0.25, 0, 1) * 0.6
    col = _mix(col, light, np.broadcast_to(hi, (h, w)))
    col *= (1 - 0.10 * v)[..., None]
    return col


def petal_art(w, h, base, mid, tip, edge_dark=0.72, vein=None):
    """花瓣：v 从根（0）到尖（1）三段渐变，u 两边压暗，中间一道浅色叶脉。"""
    u, v = _grid(w, h)
    vb = np.broadcast_to(v, (h, w))
    col = np.where((vb < 0.5)[..., None], _mix(base, mid, np.clip(vb / 0.5, 0, 1)), _mix(mid, tip, np.clip((vb - 0.5) / 0.5, 0, 1)))
    edge = (1 - (2 * u - 1) ** 6)
    col *= (edge_dark + (1 - edge_dark) * np.broadcast_to(edge, (h, w)))[..., None]
    if vein is not None:
        vein_m = np.exp(-((u - 0.5) * 14) ** 2) * (1 - vb) * 0.55
        col = _mix(col, vein, vein_m)
    return col


def bell_art(w, h, base, tip, stripes=5):
    u, v = _grid(w, h)
    col = _mix(base, tip, np.broadcast_to(v, (h, w)))
    stripe = 0.86 + 0.14 * (0.5 + 0.5 * np.cos(u * 2 * math.pi * stripes))
    col *= np.broadcast_to(stripe, (h, w))[..., None]
    return col


def compose_texture(base_png, fill, regions):
    """128×128 RGB：原 64×64 贴左上角，其余先铺 fill，再按 regions 贴各块画好的图。"""
    canvas = np.empty((TEX, TEX, 3), dtype=np.float64)
    canvas[:] = fill
    base = np.asarray(Image.open(base_png).convert("RGB"), dtype=np.float64)
    canvas[:base.shape[0], :base.shape[1]] = base
    for (x0, y0, x1, y1), art in regions:
        canvas[y0:y1, x0:x1] = art
    rgba = np.concatenate([np.clip(canvas, 0, 255).astype(np.uint8), np.full((TEX, TEX, 1), 255, np.uint8)], axis=2)
    return rgba


def write_dds(template_64, rgba, out_path):
    """用母本 64×64 R5G6B5 的头、把宽高改成 128 写出（和 ch01B0049.dds 这类 128×128 R5G6B5 原版文件同头）。"""
    hdr = bytearray(open(template_64, "rb").read()[:128])
    if hdr[:4] != b"DDS ":
        raise SystemExit("模板不是 DDS")
    struct.pack_into("<II", hdr, 12, TEX, TEX)
    template = bytes(hdr) + bytes(TEX * TEX * 2)
    blob = dds_edit.encode(template, rgba)
    if len(blob) != 128 + TEX * TEX * 2:
        raise SystemExit("DDS 长度不对")
    return _write_if_changed(out_path, blob)


def _write_if_changed(path, blob):
    try:
        same = open(path, "rb").read() == blob
    except OSError:
        same = False
    if not same:
        with open(path, "wb") as fh:
            fh.write(blob)
    return not same


# ---------------------------------------------------------------------------
# 头发件
# ---------------------------------------------------------------------------

HAIR_STRAND = (66, 2, 94, 126)
HAIR_HORN = (98, 2, 126, 62)
HAIR_LEAF = (98, 66, 126, 126)


def build_hair(mtn, dry):
    tpl_blob = open(os.path.join(CH01, "ch0100000.msh"), "rb").read()
    tpl = mshtool.parse(tpl_blob, "ch0100000.msh")
    donors = [mshtool.load(os.path.join(CH01, f)) for f in ("ch0100002.msh", "ch0100001.msh")]
    bp = BindPose(tpl, donors, mtn)
    b = Builder(tpl, "ch0300000.dds", bp)
    strand = region(*HAIR_STRAND)
    horn = region(*HAIR_HORN)
    leaf = region(*HAIR_LEAF)

    for s, S in ((1, "L"), (-1, "R")):
        # ---- 辫子：沿 Hairtail 链 7 节 + 梢 --------------------------------------------
        chain = ["Bone_Hairtail_%s%02d" % (S, i) for i in range(1, 8)]
        C = [bp.origin(n) for n in chain]
        C.append(C[-1] + bp.axis(chain[-1]) * 2.4)
        radii = [0.6, 0.66, 0.62, 0.56, 0.5, 0.42, 0.3, 0.0]
        W = [[(n, 1.0)] for n in chain] + [[(chain[-1], 1.0)]]
        b.tube(C, radii, 4, W, strand, flatten=(1.0, 0.68), side_hint=(0, 1, 0), twist=0.55 * s)

        # ---- 脸侧长发：Hair_S01 上段 / S02 下段 ----------------------------------------
        h1, h2 = "Bone_Hair_%s01" % S, "Bone_Hair_%s02" % S
        C = [(s * 4.15, 29.0, -0.55), (s * 4.35, 26.05, -1.45), (s * 4.3, 23.6, -2.2),
             (s * 4.15, 21.2, -2.7), (s * 4.0, 19.0, -2.85), (s * 3.9, 17.0, -2.7)]
        radii = [0.72, 0.8, 0.72, 0.6, 0.42, 0.0]
        W = [[(h1, 1.0)], [(h2, 1.0)], [(h2, 1.0)], [(h2, 1.0)], [(h2, 1.0)], [(h2, 1.0)]]
        b.tube(C, radii, 4, W, strand, flatten=(1.0, 0.55), side_hint=(1, 0, 0), twist=0.12 * s)

        # ---- 触角发：兔耳骨链，先外弓再往里回勾 ---------------------------------------------
        e1, e2, e3 = ("Bone_RabbitEar_%s%02d" % (S, i) for i in (1, 2, 3))
        E1, E2, E3 = bp.origin(e1), bp.origin(e2), bp.origin(e3)
        C = [E1, lerp(E1, E2, 0.5) + (s * 0.3, 0, 0.05), E2 + (s * 0.75, 0, 0.15),
             lerp(E2, E3, 0.5) + (s * 1.3, 0, 0.35), E3 + (s * 1.55, 0.0, 0.65),
             E3 + (s * 1.35, 1.5, 0.95), E3 + (s * 0.8, 2.6, 1.1)]
        radii = [0.3, 0.29, 0.27, 0.24, 0.2, 0.15, 0.0]
        W = [[(e1, 1.0)], [(e1, 0.5), (e2, 0.5)]] + [[(e2, 1.0)]] * 5
        b.tube(C, radii, 4, W, strand, side_hint=(1, 0, 0))

        # ---- 角状发簇：贴头，扁的火焰形，弯向上后外 ------------------------------------------
        H = [("Bip01_Head", 1.0)]
        B0 = np.array((s * 4.0, 32.3, 1.5))
        C = [B0, B0 + (s * 0.8, 1.3, 0.9), B0 + (s * 1.7, 2.7, 1.9), B0 + (s * 2.5, 4.2, 3.1)]
        b.tube(C, [1.5, 1.2, 0.7, 0.0], 5, [H] * 4, horn, flatten=(1.0, 0.6), side_hint=(0, 0, 1), cap_start=True)

        # ---- 叶片 ×2：黄绿，从发簇后面探出 ------------------------------------------------
        b.petal([((s * 4.0, 32.6, 2.5), (s * 0.7, 0.2, -0.7), 0.45, H),
                 ((s * 4.9, 34.3, 3.9), (s * 0.7, 0.2, -0.7), 0.62, H)],
                ((s * 5.5, 36.1, 5.1), H), leaf, hint=(s * 0.5, 0.6, -0.2), bulge=0.12)
        b.petal([((s * 3.5, 32.2, 2.7), (s * 0.9, 0.1, -0.4), 0.4, H),
                 ((s * 3.9, 33.6, 4.4), (s * 0.9, 0.1, -0.4), 0.55, H)],
                ((s * 4.2, 34.9, 5.9), H), leaf, hint=(s * 0.4, 0.7, -0.1), bulge=0.1)

    m = b.finish()
    regions = [
        (HAIR_STRAND, strand_art(HAIR_STRAND[2] - HAIR_STRAND[0], HAIR_STRAND[3] - HAIR_STRAND[1])),
        (HAIR_HORN, petal_art(HAIR_HORN[2] - HAIR_HORN[0], HAIR_HORN[3] - HAIR_HORN[1],
                              (150, 10, 84), (200, 20, 120), (236, 70, 150), edge_dark=0.7, vein=(244, 130, 180))),
        (HAIR_LEAF, petal_art(HAIR_LEAF[2] - HAIR_LEAF[0], HAIR_LEAF[3] - HAIR_LEAF[1],
                              (120, 170, 40), (170, 210, 60), (222, 236, 96), edge_dark=0.68, vein=(238, 246, 150))),
    ]
    rgba = compose_texture(os.path.join(OUT_PNG, "ch0300000.png"), (222, 220, 232), regions)
    return b, m, rgba


# ---------------------------------------------------------------------------
# 身体件
# ---------------------------------------------------------------------------

BODY_PETAL_M = (66, 2, 126, 62)
BODY_PETAL_P = (2, 66, 62, 126)
BODY_BELL = (66, 66, 126, 126)


def build_body(mtn, dry):
    tpl = mshtool.parse(open(os.path.join(CH01, "ch01B0000.msh"), "rb").read(), "ch01B0000.msh")
    donors = [mshtool.load(os.path.join(CH01, f)) for f in ("ch01B0001.msh", "ch01B0002.msh", "ch01B0006.msh")]
    bp = BindPose(tpl, donors, mtn)
    b = Builder(tpl, "ch03B0000.dds", bp)
    petal_m = region(*BODY_PETAL_M)
    petal_p = region(*BODY_PETAL_P)
    bell = region(*BODY_BELL)
    up = np.array((0, 1.0, 0))

    # ---- 裙摆 8 片花瓣：腰 X01 / 中 X02 / 尖 X02+X03 ------------------------------------
    # 原画：正面中央一片短的品红内瓣，两侧和后面是长的淡粉外瓣。
    # 原版连衣裙下摆半径 5.5，花瓣中排必须放到 6.2 以外，否则裙面从花瓣里透出来（第一版踩过）。
    for d in ("F", "FL", "L", "BL", "B", "BR", "R", "FR"):
        x1, x2, x3 = ("Bone_Skirt_%s%02d" % (d, i) for i in (1, 2, 3))
        o1, o2, o3 = bp.origin(x1), bp.origin(x2), bp.origin(x3)
        radial = norm((o2 - o1) * (1, 0, 1))
        side = norm(np.cross(up, radial))
        if d == "F":
            rows = [(o1 + radial * 0.55, side, 1.5, [(x1, 1.0)]),
                    (o2 + radial * 0.75, side, 2.2, [(x2, 1.0)])]
            tip = (lerp(o2, o3, 0.35) + radial * 0.75, [(x2, 0.7), (x3, 0.3)])
            b.petal(rows, tip, petal_m, hint=radial, bulge=0.3)
        else:
            long = d in ("FL", "FR", "L", "R")
            rows = [(o1 + radial * 0.5, side, 1.7, [(x1, 1.0)]),
                    (o2 + radial * 0.95, side, 2.9, [(x2, 1.0)]),
                    (lerp(o2, o3, 0.45) + radial * 0.85, side, 2.3, [(x2, 0.55), (x3, 0.45)])]
            tip = (lerp(o2, o3, 0.95 if long else 0.7) + radial * 0.6, [(x3, 1.0)])
            b.petal(rows, tip, petal_p, hint=radial, bulge=0.35)

    for s, S in ((1, "L"), (-1, "R")):
        sp2 = "Bip01_Spine2"
        wing = "Bone_Wing_%s01" % S
        clav, uarm, farm, hand = ("Bip01_%s_%s" % (S, n) for n in ("Clavicle", "UpperArm", "Forearm", "Hand"))

        # ---- 背后大花瓣：顶排 Spine2，往下 Wing ---------------------------------------
        side = np.array((1.0, 0, 0))
        rows = [((s * 1.8, 22.6, 1.9), side, 1.4, [(sp2, 1.0)]),
                ((s * 3.0, 20.5, 3.1), side, 2.5, [(sp2, 0.4), (wing, 0.6)]),
                ((s * 3.5, 18.2, 3.7), side, 2.9, [(wing, 1.0)]),
                ((s * 3.3, 15.9, 3.7), side, 2.4, [(wing, 1.0)])]
        b.petal(rows, ((s * 2.9, 13.6, 3.3), [(wing, 1.0)]), petal_m, hint=(s * 0.3, 0, 1.0), bulge=0.4)

        # ---- 上臂花瓣：披在上臂外上侧，垂过肘 --------------------------------------------
        U, F, Hd = bp.origin(uarm), bp.origin(farm), bp.origin(hand)
        a = norm(F - U)
        o = norm(np.cross(a, (0, 0, 1.0)))
        if o[1] < 0:
            o = -o
        rows = [(U - a * 0.3 + o * 1.0 + (0, 0.2, 0), (0, 0, 1.0), 1.5, [(clav, 0.35), (uarm, 0.65)]),
                (U + a * 1.4 + o * 1.2, (0, 0, 1.0), 2.2, [(uarm, 1.0)]),
                (U + a * 3.0 + o * 1.0, (0, 0, 1.0), 2.1, [(uarm, 1.0)])]
        b.petal(rows, (U + a * 4.6 + o * 0.35, [(uarm, 0.65), (farm, 0.35)]), petal_m, hint=o, bulge=0.45)

        # ---- 前臂喇叭花瓣袖：锯齿边，双面 --------------------------------------------------
        a2 = norm(Hd - F)
        C = [F + a2 * 0.5, F + a2 * 1.9, F + a2 * 3.4, F + a2 * 4.4]
        W = [[(farm, 1.0)]] * 4
        b.tube(C, [0.72, 1.15, 1.7, 2.05], 10, W, bell, side_hint=(0, 0, 1), double=True, zigzag=(1.0, 0.35))

    m = b.finish()
    regions = [
        (BODY_PETAL_M, petal_art(60, 60, (160, 10, 92), (212, 22, 128), (240, 84, 158), edge_dark=0.7, vein=(244, 120, 176))),
        (BODY_PETAL_P, petal_art(60, 60, (246, 206, 222), (250, 232, 240), (255, 251, 253), edge_dark=0.9, vein=(246, 196, 214))),
        (BODY_BELL, bell_art(60, 60, (150, 10, 86), (236, 76, 152))),
    ]
    rgba = compose_texture(os.path.join(OUT_PNG, "ch03B0000.png"), (246, 222, 232), regions)
    return b, m, rgba


# ---------------------------------------------------------------------------
# 校验 + 渲染
# ---------------------------------------------------------------------------

def verify(b, m, mtn):
    """写出前的硬校验：能崩的都在这里拦。"""
    blob = mshtool.write(m)
    chk = mshtool.parse(blob, m.path)
    assert mshtool.write(chk) == blob, "自 round-trip 不一致"
    tpl = b.template
    assert chk.nv == len(b.pos) and chk.ni == 3 * len(b.faces)
    assert np.array_equal(chk.verts["pos"][:b.n_old_v], tpl.verts["pos"]), "旧顶点位置变了"
    assert np.array_equal(chk.verts["nrm"][:b.n_old_v], tpl.verts["nrm"]), "旧法线变了"
    assert np.allclose(chk.verts["uv"][:b.n_old_v], tpl.verts["uv"] * 0.5, atol=1e-6), "旧 UV 不是 ×0.5"
    assert np.array_equal(chk.indices[:3 * b.n_old_f], tpl.indices), "旧面变了"
    names = set(mtn.names)
    for name, ids, ws in mshtool.bone_table(chk):
        assert name in names, "骨骼 %s 不在骨架里" % name
        assert ids.max() < chk.nv
    old_adj = tpl.adjacency[4:4 + 40 * b.n_old_f]
    new_adj = chk.adjacency[4:4 + 40 * b.n_old_f]
    assert old_adj == new_adj, "旧面的邻接表条目变了（新顶点和旧顶点位置重合了？）"
    cnt = np.zeros(chk.nv, int)
    wsum = np.zeros(chk.nv)
    for _, ids, ws in mshtool.bone_table(chk):
        cnt[ids] += 1
        wsum[ids] += ws
    assert cnt.min() >= 1 and cnt.max() <= 3 and abs(wsum - 1).max() < 1e-4
    old_bones = [n for n, _ in tpl.bones]
    new_bones = [n for n, _ in chk.bones]
    for n in old_bones:
        assert n in new_bones
    # 旧骨头的 offset 逐字节不变
    old_off = mshtool.bone_offsets(tpl)
    new_off = mshtool.bone_offsets(chk)
    for n in old_bones:
        assert np.array_equal(old_off[n], new_off[n]), "旧骨 %s 的 offset 变了" % n
    return blob, chk, new_bones


def part_meshes(names, override=None, root=None):
    """载入若干部件（网格 + 同名贴图）。`root` 默认是爱琳的目录，渲对照图时可指向别的角色。"""
    root = root or CH03
    parts = []
    for n in names:
        if override and n in override:
            m, rgba = override[n]
        else:
            m = mshtool.load(os.path.join(root, n + ".msh"))
            rgba = dds_edit.load_rgba(os.path.join(root, m.textures[0]))[1]
        parts.append((m, rgba))
    return parts


def posed(parts, mtn, t):
    """把各部件蒙皮到 mtn 的时刻 t（tick），返回可直接喂 rasterize 的 (Mesh 副本, 贴图)。"""
    W = mtntool.world_mats(mtn, t)
    out = []
    for m, tex in parts:
        p = mshtool.skin_positions(m, W, 1.0 / SCENE_SCALE)
        mm = mshtool.Mesh()
        for k in mshtool.Mesh.__slots__:
            setattr(mm, k, getattr(m, k))
        mm.verts = m.verts.copy()
        mm.verts["pos"] = p.astype("<f4")
        out.append((mm, tex))
    return out


def labeled(img, text, width, height):
    canvas = Image.new("RGB", (width, height), (30, 30, 34))
    scale = min((width - 8) / img.width, (height - 28) / img.height)
    im = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    canvas.paste(im, ((width - im.width) // 2, 24 + (height - 24 - im.height) // 2))
    ImageDraw.Draw(canvas).text((6, 6), text, fill=(230, 230, 230))
    return canvas


def render_report(override, mtn_dir):
    size = 400
    names = ["ch0300000", "ch03H0000", "ch03B0000", "ch03G0000", "ch03L0000", "ch03S0000"]
    parts = part_meshes(names, override)
    allpos = np.concatenate([m.verts["pos"] for m, _ in parts])
    lo, hi = allpos.min(0), allpos.max(0)
    # 绑定姿态里辫子向后外伸得很远，包围盒按躯干算，别让辫子把人缩成一小团
    bbox = (np.array([-12.0, min(lo[1], -0.5), -8.0]), np.array([12.0, hi[1] + 0.5, 12.0]))
    bbox2 = (np.array([-11.0, -0.5, -11.0]), np.array([11.0, 40.0, 11.0]))
    panels = []
    concept = Image.open(CONCEPT).convert("RGB") if os.path.exists(CONCEPT) else None
    if concept is not None:
        panels.append(labeled(concept, "concept", size, size + 24))
    for view in ("front", "side", "back"):
        im = Image.fromarray(mshtool.rasterize(parts, view=view, size=size, bbox=bbox))
        panels.append(labeled(im, "bind pose · %s" % view, size, size + 24))
    sheet = Image.new("RGB", (sum(p.width for p in panels), size + 24), (30, 30, 34))
    x = 0
    for p in panels:
        sheet.paste(p, (x, 0))
        x += p.width
    out1 = os.path.join(HERE, "compare_C_bind.png")
    sheet.save(out1)

    rows = []
    for anim, frame in (("Stand00", 0), ("Run-F00", 8), ("Jump", 6), ("Win", 20)):
        path = os.path.join(mtn_dir, "ch03@%s.mtn" % anim)
        if not os.path.exists(path):
            continue
        mtn = mtntool.parse(path)
        t = min(frame * mtntool.TICKS_PER_FRAME, mtn.duration * mtn.ticks_per_sec)
        pp = posed(parts, mtn, t)
        row = []
        for view in ("front", "side", "back", "rot35"):
            im = Image.fromarray(mshtool.rasterize(pp, view=view, size=size, bbox=bbox2))
            row.append(labeled(im, "%s f%d · %s" % (anim, frame, view), size, size + 24))
        rows.append(row)
    if rows:
        sheet = Image.new("RGB", (size * 4, (size + 24) * len(rows)), (30, 30, 34))
        for r, row in enumerate(rows):
            for c, p in enumerate(row):
                sheet.paste(p, (c * size, r * (size + 24)))
        out2 = os.path.join(HERE, "compare_C_posed.png")
        sheet.save(out2)
        print("->", out2)
        # 主对照图：原画 | 站立动作第 0 帧（最接近游戏里的样子）正 / 侧 / 背 / 斜
        first = ([panels[0]] if concept is not None else []) + rows[0]
        sheet = Image.new("RGB", (sum(p.width for p in first), size + 24), (30, 30, 34))
        x = 0
        for p in first:
            sheet.paste(p, (x, 0))
            x += p.width
        out0 = os.path.join(HERE, "compare_C.png")
        sheet.save(out0)
        print("->", out0)
    print("->", out1)

    # 贴图
    sheet = Image.new("RGB", (2 * 264, 264), (30, 30, 34))
    for i, n in enumerate(["ch0300000", "ch03B0000"]):
        rgba = override[n][1] if override and n in override else dds_edit.load_rgba(os.path.join(CH03, n + ".dds"))[1]
        sheet.paste(Image.fromarray(rgba[..., :3]).resize((256, 256), Image.NEAREST), (4 + i * 264, 4))
    out3 = os.path.join(HERE, "textures_C.png")
    sheet.save(out3)
    print("->", out3)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=["hair", "body"])
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args(argv)

    mtn = mtntool.parse(os.path.join(CH03, "ch03@Stand00.mtn"))
    override = {}
    for kind, fn, name in (("hair", build_hair, "ch0300000"), ("body", build_body, "ch03B0000")):
        if args.only and args.only != kind:
            continue
        b, m, rgba = fn(mtn, args.dry_run)
        blob, chk, bones = verify(b, m, mtn)
        new_bones = [n for n in bones if n not in [x for x, _ in b.template.bones]]
        print("%s: 顶点 %d -> %d，面 %d -> %d，骨 %d -> %d（新增 %s）；%d B"
              % (name, b.n_old_v, chk.nv, b.n_old_f, chk.ni // 3, len(b.template.bones), len(bones), new_bones, len(blob)))
        src = {n: b.bp.source.get(n, "?") for n in new_bones}
        print("   新骨 offset 来源：%s" % src)
        override[name] = (chk, rgba)
        if not args.dry_run:
            w1 = _write_if_changed(os.path.join(CH03, name + ".msh"), blob)
            w2 = write_dds(os.path.join(CH01, "ch01" + name[4:] + ".dds"), rgba, os.path.join(CH03, name + ".dds"))
            print("   写出：.msh %s，.dds %s" % ("已更新" if w1 else "未变", "已更新" if w2 else "未变"))
    if not args.no_render:
        render_report(override, CH03)
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
