#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weapons.py —— 爱琳的三把手持武器 + 两个弹体（X1 M7 阶段 E）。

    C:\\Python314\\python.exe tools/ch03_skin/weapons.py                # 写 5 个 .msh + 5 个 .dds，并渲对照图
    C:\\Python314\\python.exe tools/ch03_skin/weapons.py --dry-run      # 只渲对照图，不写
    C:\\Python314\\python.exe tools/ch03_skin/weapons.py --only W0001   # 只做一件（W0001 / W0002 / W0003 / D0002 / D0003A）

## 在重建链里的位置（★ 最后一步）

    mkchar.py（克隆母本）→ recolor.py → ears.py → geometry.py → **weapons.py（本脚本）**

本脚本只读**只读母本** ch01 的 5 个 `.msh`（抄头 / 标志 / 材质 float / 骨骼 offset）和 ch03 的骨架
`.mtn`（和 ch01 逐字节相同），不读 ch03 里任何被改过的东西 ⇒ 幂等；和 geometry.py 互不依赖，
放最后只是为了让顺序好记。

## 做了什么（照 Images/Game/Ch003Weapons.png 三帧）

| 文件 | 内容 | 挂在哪根骨（offset 逐字节抄母本） |
|---|---|---|
| ch03W0001 | 트릭스터 Trickster：**一把双手步枪**（深灰枪托 + 绿枪身 + 绿瞄准器黄徽记 + 绿握把 + 橙护套 + 上叶翼 / 下托手 + 灰方口）。持枪动作从瓦尔基里移植（anim.py） | Bone_Wp01_R02 |
| ch03W0002 | 씨드 폭탄 Seed Bomb（手持）：紫色带斑种球 + 黄色锯齿萼 + 绿茎叶 | Bone_Wp02_02 |
| ch03W0003 | 에이리얼 슈터 Aerial Shooter：**长柄花杖**（绿 / 黄杖身 + 金环两黄叶 + 五片蓝花瓣 + 黄花蕊尖刺，花蕊尖 = 枪口） | Bone_Wp03_01 |
| ch03D0002 | 씨드 폭탄（弹体）：同 W0002，立起来，原点在球心下方 | Bone_C01W02_01 |
| ch03D0003A | 포션 药水 / 回血图腾：蓝玻璃圆底瓶 + 金腰带 + 金颈环 + 绿木塞叶 + 金底座（落地能立住） | Bone_C01W03_01 |

★ 步枪和手杖的摆放框架（在参考姿势的世界坐标里定义，换算到骨骼局部）以及枪口点骨的位置都在 `rig.py`，
anim.py 拿同一份数字去打 `*_FirePoint` 静态骨补丁 —— 改造型位置只改 rig.py，两边自然一致。
步枪 / 手杖的手持对照图用的是 StandN / AttackN / Reload01（anim.py 移植后的动作，先跑 anim.py 再跑本脚本才对得上）。

## 坐标：一律在**骨骼局部（场景单位）**里设计（FINDINGS §18）

每根武器骨的 `inv(offset)` 就是「骨骼局部 → 网格空间」的矩阵（W0001 里带 5.849 的均匀缩放，其余为 1）。
卡希尔的原件告诉我们（tools/mshtool.py 的 bone_offsets + mtntool 的骨架树，2026-09-15 实测）：

- `Bone_Wp01_R02`：枪管从原点向 +x **抬 25°** 指向 FirePoint(35.6, 0, 14.3)，击锤在 (-21, 0, 6..16)，
  握把在 (-3, 0, -12)。**L02 的 bind 是 R02 的镜像（det < 0）**，左枪的局部坐标和右枪逐点相同
  ⇒ 同一份几何放两次，左边由 det 自动反绕向。
- `Bone_Wp02_02`：瓶子轴 = +x（FirePoint 在 +22），球心在原点附近。
- `Bone_Wp03_01`：原点在手心（原版箱子的提梁沿 z 穿过原点），炮口朝 -z（FirePoint (12.4, 0, -36.2)），主体在 x 2..30。
- 弹体骨 offset 是单位阵，原点在物体中心（`Models/Items/` 的道具也都是原点居中的）。

## 安全边界

- 骨骼名只用母本文件里已有的（子集），offset 逐字节抄；写出前逐个断言：手持件的骨在 229 节点骨架里，
  弹体的骨是 `Bone_C01W*`（弹体对象自己的帧，不在骨架树里，原版 37 个武器网格全这样，别改名）。
- 每顶点 1 根骨、权重 1.0；面绕向 = 外法线（写出前统计几何法线和顶点法线的一致率 ≥ 97%）。
- 邻接表由 mshtool.build_adjacency 生成（两两配对规则，见 §17），双面薄片不会再出现正面配反面。
- 贴图 128×128 R5G6B5（母本 64×64 的头改宽高；`ch03B0000.dds` 已实机证明这种头能读）。
"""
from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
sys.path.insert(0, HERE)
import dds_edit  # noqa: E402
import mshtool  # noqa: E402
import mtntool  # noqa: E402
import geometry  # noqa: E402
from geometry import norm, petal_art, region, _write_if_changed, write_dds, _grid, _mix  # noqa: E402

CHARS = os.path.join(ROOT, "game_patched", "Pack_develop", "Models", "Characters")
CH01 = os.path.join(CHARS, "ch01")
CH03 = os.path.join(CHARS, "ch03")
REF_PNG = os.path.join(ROOT, "game_patched", "Pack_develop", "Images", "Game", "Ch003Weapons.png")
REF_RECTS = [(1, 1, 137, 113), (139, 1, 275, 113), (277, 1, 413, 113)]   # Ch003Weapons.smf 的三帧
TEX = 128


# ---------------------------------------------------------------------------
# 构造器：从空网格开始，几何写在「设计坐标」里，place() 决定落到哪根骨的局部空间
# ---------------------------------------------------------------------------

class BoneBind:
    """给 Builder.finish() 用的最小绑定姿态：名字表来自骨架，offset 逐字节来自母本文件。"""

    def __init__(self, template, mtn):
        # 弹体的骨（Bone_C01W02_01 / Bone_C01W03_01）不在角色骨架里 —— 那是弹体对象自己的帧，
        # 运行时挂接（原版 ch01 的 37 个武器网格全这样）。规则只有一条：**只用母本文件里已有的骨骼名**。
        self.off = mshtool.bone_offsets(template)
        self.names = set(mtn.names) | set(self.off)

    def offset(self, name):
        return self.off[name]

    def bind(self, name):
        return np.linalg.inv(self.off[name])


class Part(geometry.Builder):
    def __init__(self, template, texture, bind):
        self.template = template
        self.texture = texture
        self.bp = bind
        self.pos, self.nrm, self.uv, self.w, self.faces = [], [], [], [], []
        self.n_old_v = self.n_old_f = 0
        self.bone_order = []
        self.M = np.eye(4)
        self.flip = False
        self.bone = None

    def place(self, bone, origin=(0, 0, 0), f=(1, 0, 0), u=(0, 0, 1)):
        """设计坐标 (a, b, c) = a·f + b·u + c·s + origin（骨骼局部、场景单位），再乘 bind 进网格空间。

        s = f × u（右手系），所以只有 bind 本身是镜像时（W0001 的 L02）才需要反绕向 —— 按 det 自动判。
        """
        f = norm(f)
        u = np.asarray(u, dtype=np.float64)
        u = norm(u - (u @ f) * f)
        s = np.cross(f, u)
        G = np.eye(4)
        G[0, :3], G[1, :3], G[2, :3], G[3, :3] = f, u, s, origin
        self.M = G @ self.bp.bind(bone)
        self.flip = bool(np.linalg.det(self.M[:3, :3]) < 0)
        self.bone = bone
        return self

    @property
    def W(self):
        return [(self.bone, 1.0)]

    def v(self, p, n, uv, weights):
        p4 = np.array([*p, 1.0], dtype=np.float64) @ self.M
        n3 = np.asarray(n, dtype=np.float64) @ self.M[:3, :3]
        return super().v(p4[:3], n3, uv, weights)

    def tri(self, a, b, c):
        self.faces.append((a, c, b) if self.flip else (a, b, c))

    def tube(self, centers, radii, sides, weights, uvreg, inside=False, **kw):
        """同 Builder.tube；`inside=True` 做内壁（法线朝内、反绕向）。"""
        if not inside:
            return super().tube(centers, radii, sides, weights, uvreg, **kw)
        v0 = len(self.pos)
        self.flip = not self.flip
        super().tube(centers, radii, sides, weights, uvreg, **kw)
        self.flip = not self.flip
        for i in range(v0, len(self.pos)):
            self.nrm[i] = tuple(-x for x in self.nrm[i])

    def disc(self, center, axis, radius, sides, uvreg, weights, side_hint=None):
        """圆盘（扇），法线 = axis。"""
        C = np.asarray(center, dtype=np.float64)
        T = norm(axis)
        hint = np.asarray(side_hint if side_hint is not None else ((0, 1, 0) if abs(T[1]) < 0.9 else (1, 0, 0)), float)
        N = norm(hint - (hint @ T) * T)
        B = np.cross(T, N)
        u0, v0, u1, v1 = uvreg
        uc, vc, ru, rv = 0.5 * (u0 + u1), 0.5 * (v0 + v1), 0.5 * (u1 - u0), 0.5 * (v1 - v0)
        c = self.v(C, T, (uc, vc), weights)
        ring = []
        for j in range(sides):
            th = 2 * math.pi * j / sides
            ring.append(self.v(C + radius * (math.cos(th) * N + math.sin(th) * B), T,
                               (uc + ru * math.cos(th), vc + rv * math.sin(th)), weights))
        for j in range(sides):
            self.tri(c, ring[j], ring[(j + 1) % sides])

    def box(self, center, half, uvreg, weights, axes=None, uv_faces=None):
        """长方体（6 面 24 顶点）。`axes` 3×3 右手系（行向量），默认设计坐标轴；`uv_faces` 可给单面换贴图区。"""
        C = np.asarray(center, dtype=np.float64)
        A = np.eye(3) if axes is None else np.asarray(axes, dtype=np.float64)
        h = np.asarray(half, dtype=np.float64)
        for axis in range(3):
            i1, i2 = (axis + 1) % 3, (axis + 2) % 3
            for sign in (1, -1):
                n = sign * A[axis]
                reg = (uv_faces or {}).get(("+" if sign > 0 else "-") + "xyz"[axis], uvreg)
                u0, v0, u1, v1 = reg
                e1, e2 = A[i1] * h[i1], A[i2] * h[i2]
                face_c = C + n * h[axis]
                ids = [self.v(face_c + a * e1 + b * e2, n, (u0 + (u1 - u0) * (a + 1) / 2, v0 + (v1 - v0) * (b + 1) / 2), weights)
                       for a, b in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
                if sign > 0:
                    self.quad(ids[0], ids[1], ids[2], ids[3])
                else:
                    self.quad(ids[0], ids[3], ids[2], ids[1])

    def sphere(self, center, r, lat, lon, uvreg, weights, axis=(1, 0, 0), squash=1.0):
        """球（沿 axis 一圈圈的管子；第一圈用 cap_start 封底，末端收成尖）。"""
        C = np.asarray(center, dtype=np.float64)
        T = norm(axis)
        thetas = [math.pi * k / lat for k in range(1, lat + 1)]
        centers = [C - T * (r * squash * math.cos(t)) for t in thetas]
        radii = [r * math.sin(t) for t in thetas]
        radii[-1] = 0.0
        self.tube(centers, radii, lon, [weights] * len(centers), uvreg, cap_start=True)


# ---------------------------------------------------------------------------
# 贴图
# ---------------------------------------------------------------------------

def flat(w, h, c):
    out = np.empty((h, w, 3), dtype=np.float64)
    out[:] = c
    return out


def vgrad(w, h, c0, c1):
    u, v = _grid(w, h)
    return _mix(c0, c1, np.broadcast_to(v, (h, w)))


def facet_art(w, h, c_light, c_dark, n, band, band_from):
    """折纸喇叭：绕一圈 n 个面，明暗交替（管子每边正好一格），花口一圈金边。"""
    u, v = _grid(w, h)
    U, V = np.broadcast_to(u, (h, w)), np.broadcast_to(v, (h, w))
    k = np.floor(U * n).astype(int) % 2
    col = np.where(k[..., None] == 0, np.asarray(c_light, float), np.asarray(c_dark, float))
    col = col * (0.9 + 0.1 * V)[..., None]
    m = np.clip((V - band_from) / 0.03, 0, 1)
    return _mix(col, band, m)


def banded(w, h, c0, c1, band, band_from):
    """纵向渐变，末端（v >= band_from）换成一圈纯色（喇叭花口的金边）。"""
    col = vgrad(w, h, c0, c1)
    u, v = _grid(w, h)
    m = np.clip((np.broadcast_to(v, (h, w)) - band_from) / 0.03, 0, 1)
    return _mix(col, band, m)


def metal_art(w, h, c_hi, c_lo):
    """金属：纵向渐变 + 一道高光带。"""
    u, v = _grid(w, h)
    col = vgrad(w, h, c_hi, c_lo)
    hi = np.exp(-((np.broadcast_to(v, (h, w)) - 0.3) * 6) ** 2) * 0.35
    return _mix(col, (255, 255, 255), hi)


def bore_art(w, h, c_face, c_bore):
    """炮口面：灰面中间一个黑方洞，洞口一圈更暗的斜面。"""
    u, v = _grid(w, h)
    col = flat(w, h, c_face)
    d = np.maximum(np.abs(np.broadcast_to(u, (h, w)) - 0.5), np.abs(np.broadcast_to(v, (h, w)) - 0.5))
    ring = np.clip((0.36 - d) / 0.05, 0, 1)
    col = _mix(col, (60, 60, 66), ring)
    hole = np.clip((0.27 - d) / 0.03, 0, 1)
    return _mix(col, c_bore, hole)


def spots_art(w, h, base, spot, n=9, seed=3, rmin=0.07, rmax=0.17):
    """带斑点的种球：u 方向周期（管子的接缝处斑点接得上）。"""
    rng = np.random.default_rng(seed)
    u, v = _grid(w, h)
    U, V = np.broadcast_to(u, (h, w)), np.broadcast_to(v, (h, w))
    col = vgrad(w, h, tuple(np.asarray(base) * 1.08), tuple(np.asarray(base) * 0.85))
    for _ in range(n):
        cu, cv = rng.random(), 0.15 + 0.7 * rng.random()
        rr = rmin + (rmax - rmin) * rng.random()
        du = (U - cu + 0.5) % 1.0 - 0.5
        d = np.sqrt(du ** 2 + (V - cv) ** 2) / rr
        m = np.clip((1.15 - d) / 0.3, 0, 1)
        col = _mix(col, spot, m)
    return np.clip(col, 0, 255)


def glass_art(w, h, c0, c1):
    """玻璃：纵向渐变 + 一道竖着的高光。"""
    u, v = _grid(w, h)
    col = vgrad(w, h, c0, c1)
    hi = np.exp(-((np.broadcast_to(u, (h, w)) - 0.3) * 7) ** 2) * 0.45
    return _mix(col, (235, 248, 255), hi)


class Atlas:
    def __init__(self, fill):
        self.canvas = flat(TEX, TEX, fill)
        self.regs = {}

    def add(self, name, rect, art, inset=2.0):
        x0, y0, x1, y1 = rect
        self.canvas[y0:y1, x0:x1] = art(x1 - x0, y1 - y0)
        self.regs[name] = region(x0, y0, x1, y1, inset)
        return self.regs[name]

    def rgba(self):
        rgb = np.clip(self.canvas, 0, 255).astype(np.uint8)
        return np.concatenate([rgb, np.full((TEX, TEX, 1), 255, np.uint8)], axis=2)


# ---------------------------------------------------------------------------
# 三种造型（都在设计坐标里：a 沿 f，b 沿 u，c 沿 s）
# ---------------------------------------------------------------------------

def body_art(w, h, c_light, c_dark, band, band_v=(0.42, 0.62)):
    """绿色枪身：纵向渐变 + 中间一道深灰色嵌条（参考图里枪身侧面那条深色框）。"""
    u, v = _grid(w, h)
    V = np.broadcast_to(v, (h, w))
    col = _mix(c_light, c_dark, V)
    m = np.clip((V - band_v[0]) / 0.02, 0, 1) * np.clip((band_v[1] - V) / 0.02, 0, 1)
    return _mix(col, band, m)


def sight_art(w, h, c_green, c_yellow, c_dark):
    """瞄准器侧面：绿底 + 一枚黄色菱形徽记（外圈一圈深色描边）。"""
    u, v = _grid(w, h)
    U, V = np.broadcast_to(u, (h, w)), np.broadcast_to(v, (h, w))
    col = flat(w, h, c_green)
    d = np.abs(U - 0.5) / 0.34 + np.abs(V - 0.5) / 0.4
    col = _mix(col, c_dark, np.clip((1.15 - d) / 0.12, 0, 1))
    col = _mix(col, c_yellow, np.clip((0.95 - d) / 0.12, 0, 1))
    return col


def trickster_atlas():
    at = Atlas((70, 70, 76))
    at.add("metal", (2, 2, 42, 42), lambda w, h: metal_art(w, h, (150, 152, 160), (78, 80, 88)))
    at.add("bore", (44, 2, 84, 42), lambda w, h: bore_art(w, h, (146, 148, 156), (16, 16, 18)))
    at.add("orange", (86, 2, 126, 50), lambda w, h: vgrad(w, h, (255, 158, 44), (226, 74, 20)))
    at.add("leaf", (2, 44, 60, 126), lambda w, h: petal_art(w, h, (190, 238, 78), (150, 210, 46), (104, 168, 32), edge_dark=0.74, vein=(222, 246, 140)))
    at.add("body", (62, 52, 126, 84), lambda w, h: body_art(w, h, (180, 228, 66), (124, 182, 40), (58, 60, 66)))
    at.add("grip", (62, 86, 92, 126), lambda w, h: vgrad(w, h, (162, 214, 54), (104, 162, 34)))
    at.add("sight", (94, 86, 126, 110), lambda w, h: sight_art(w, h, (140, 196, 44), (252, 224, 66), (60, 62, 68)))
    at.add("dark", (94, 112, 126, 126), lambda w, h: metal_art(w, h, (86, 88, 94), (34, 34, 40)))
    at.add("yellow", (2, 116, 20, 126), lambda w, h: vgrad(w, h, (254, 232, 90), (236, 190, 40)))
    at.add("gray", (22, 116, 60, 126), lambda w, h: metal_art(w, h, (118, 120, 126), (62, 64, 70)))
    return at


def trickster_rifle(p, R):
    """一把双手步枪。设计坐标（rig.RIFLE）：a 沿枪身朝枪口，b 朝上，c 朝角色左侧；原点在右手正上方的枪身轴线上。

    从后往前：深灰枪托管 → 绿色枪身（侧面深灰嵌条、上有绿色瞄准器 + 黄色徽记、下挂绿色握把）
    → 橙色枪管护套（上一片绿叶翼、下一片大的流线托手）→ 深灰方口（前脸黑洞，FirePoint 就在前脸外 a=31.5）。
    """
    W = p.W
    # 枪托：深灰圆管 + 后端稍粗的端帽（故意短：再长就顶到左脸）
    p.tube([(-7.5, -0.4, 0), (-6.0, -0.4, 0), (-5.6, -0.4, 0), (-1.5, -0.2, 0)], [2.6, 2.6, 2.1, 2.1], 8, [W] * 4, R["dark"], side_hint=(0, 1, 0), cap_start=True)
    # 枪身：绿色主体（侧面 ±c 带深灰嵌条），前端接一小段灰色机匣
    p.box((5.5, -0.3, 0), (7.5, 4.4, 4.0), R["body"], W,
          uv_faces={"+y": R["grip"], "-y": R["grip"], "+x": R["gray"], "-x": R["gray"]})
    p.box((14.2, -0.2, 0), (1.3, 3.6, 3.4), R["gray"], W)
    # 瞄准器：绿盒子（两侧黄色菱形徽记）+ 顶上一小块黄色准星 + 前端一粒黄色准星珠
    p.box((4.5, 5.9, 0), (3.0, 1.9, 1.8), R["sight"], W, uv_faces={"+y": R["grip"], "-y": R["grip"], "+x": R["grip"], "-x": R["grip"]})
    p.box((6.3, 8.1, 0), (0.8, 0.5, 0.8), R["yellow"], W)
    p.box((12.5, 4.3, 0), (0.6, 1.0, 0.5), R["yellow"], W)
    # 握把：往后下方斜 18°，底部一截深灰
    ang = math.radians(18.0)
    d = np.array((-math.sin(ang), -math.cos(ang), 0.0))
    s = np.array((0, 0, 1.0))
    e = np.cross(s, d)
    p.box((1.5, -8.8, 0), (4.0, 2.8, 2.3), R["grip"], W, axes=np.array([d, e, s]))
    p.box((0.2, -13.0, 0), (1.3, 2.6, 2.5), R["dark"], W, axes=np.array([d, e, s]))
    # 扳机护圈 + 扳机
    p.box((8.0, -5.4, 0), (2.2, 0.7, 0.5), R["dark"], W)
    p.box((6.2, -6.6, 0), (0.5, 1.3, 0.4), R["dark"], W)
    # 橙色枪管护套：向前微张的圆锥，9 边
    p.tube([(15.5, 0.2, 0), (19.5, 0.3, 0), (24.0, 0.4, 0), (29.5, 0.4, 0)], [3.8, 4.7, 5.2, 4.8], 9, [W] * 4, R["orange"], side_hint=(0, 1, 0), cap_start=True)
    # 深灰方口：前脸黑洞（前脸 a=38.2，rig.RIFLE.muzzle_a=38.3 就在它外面）
    p.box((33.8, 0.2, 0), (4.4, 4.4, 4.4), R["metal"], W, uv_faces={"+x": R["bore"]})
    # 上叶翼 / 下托手：都是**竖着的**流线叶片（叶面在 a-b 平面里，法线朝侧面 ⇒ 从侧面看是整片宽叶），
    # ★ 根在后（枪身 / 护套交界处）、**尖朝枪口**（用户 2026-09-16 指出上一版尖朝枪把，反了）。
    # 宽方向给 (1,0,0)，petal() 会把它正交到走向的垂直方向上；bulge 把中线顶出去一道棱 = 折叶
    p.petal([((14.5, 3.4, 0), (1, 0, 0), 1.6, W), ((20.5, 6.6, 0), (1, 0, 0), 2.8, W), ((26.0, 8.2, 0), (1, 0, 0), 2.2, W)],
            ((31.5, 9.6, 0), W), R["leaf"], hint=(0, 0, 1), bulge=0.5)
    p.petal([((10.0, -5.0, -0.4), (1, 0, 0), 2.0, W), ((16.0, -8.6, -0.8), (1, 0, 0), 3.8, W), ((22.0, -10.6, -1.2), (1, 0, 0), 4.2, W), ((28.5, -11.6, -1.5), (1, 0, 0), 3.0, W)],
            ((34.5, -12.6, -1.8), W), R["leaf"], hint=(0, 0, -1), bulge=0.7)
    # 顶部两侧各一片小叶（参考图主叶后面露出的那片浅绿叶），也是尖朝前
    for sc in (1, -1):
        p.petal([((15.0, 2.6, sc * 3.0), (1, 0, 0), 1.2, W), ((21.0, 4.6, sc * 5.4), (1, 0, 0), 2.2, W)],
                ((27.5, 6.2, sc * 7.0), W), R["leaf"], hint=(0, 0.4, sc), bulge=0.4)


def build_W0001(mtn):
    import rig
    tpl = mshtool.load(os.path.join(CH01, "ch01W0001.msh"))
    at = trickster_atlas()
    p = Part(tpl, "ch03W0001.dds", BoneBind(tpl, mtn))
    o, f, u = rig.frame_local(rig.RIFLE)
    p.place(rig.RIFLE["bone"], o, f, u)
    trickster_rifle(p, at.regs)
    return tpl, p, at


def seed_atlas():
    at = Atlas((120, 40, 160))
    at.add("purple", (2, 2, 66, 66), lambda w, h: spots_art(w, h, (156, 44, 208), (58, 6, 92), n=8, seed=5, rmin=0.09, rmax=0.21))
    at.add("cup", (68, 2, 126, 42), lambda w, h: vgrad(w, h, (214, 160, 32), (252, 212, 56)))
    at.add("stem", (68, 44, 100, 76), lambda w, h: vgrad(w, h, (146, 204, 52), (96, 156, 34)))
    at.add("tip", (102, 44, 126, 68), lambda w, h: flat(w, h, (26, 18, 34)))
    at.add("leaf", (2, 68, 66, 126), lambda w, h: petal_art(w, h, (142, 200, 44), (110, 176, 32), (170, 220, 70), edge_dark=0.7, vein=(200, 236, 110)))
    return at


def seed_bomb(p, R):
    """种子炸弹。设计坐标：a 沿茎（向上），球心在 a=1。"""
    W = p.W
    p.sphere((1.0, 0, 0), 9.0, 7, 12, R["purple"], W, axis=(1, 0, 0), squash=0.95)
    # 黄色锯齿萼
    p.tube([(-9.0, 0, 0), (-7.5, 0, 0), (-4.5, 0, 0), (-2.0, 0, 0)], [2.6, 5.5, 8.0, 8.9], 10, [W] * 4, R["cup"],
           side_hint=(0, 1, 0), zigzag=(2.2, 0.9), cap_start=True)
    # 绿茎（略向 +b 弯）+ 顶端黑洞
    p.tube([(9.2, 0.3, 0), (12.5, 1.0, 0), (15.5, 2.0, 0), (18.0, 3.0, 0)], [2.2, 1.9, 1.6, 1.4], 7, [W] * 4, R["stem"], side_hint=(0, 1, 0))
    p.disc((18.0, 3.0, 0), (0.8, 0.35, 0), 1.4, 7, R["tip"], W)
    # 一片叶
    p.petal([((10.5, 0.8, 0.5), (0, 0, 1), 1.2, W), ((12.5, 4.5, 1.5), (0, 0, 1), 2.4, W)],
            ((13.5, 9.0, 3.0), W), R["leaf"], hint=(1, 0, 0), bulge=0.35)


def build_W0002(mtn):
    tpl = mshtool.load(os.path.join(CH01, "ch01W0002.msh"))
    at = seed_atlas()
    p = Part(tpl, "ch03W0002.dds", BoneBind(tpl, mtn))
    p.place("Bone_Wp02_02", (0, 0, 0), (1, 0, 0), (0, 0, 1))
    seed_bomb(p, at.regs)
    return tpl, p, at


def build_D0002(mtn):
    tpl = mshtool.load(os.path.join(CH01, "ch01D0002.msh"))
    at = seed_atlas()
    p = Part(tpl, "ch03D0002.dds", BoneBind(tpl, mtn))
    p.place("Bone_C01W02_01", (0, 6, 0), (0, 1, 0), (0, 0, 1))
    seed_bomb(p, at.regs)
    return tpl, p, at


def crystal_art(w, h, c0, c1, edge_dark=0.6):
    """水晶花瓣：沿长度的亮→深渐变，两边压暗，没有叶脉；中线（棱）那一列略提亮。"""
    u, v = _grid(w, h)
    U, V = np.broadcast_to(u, (h, w)), np.broadcast_to(v, (h, w))
    col = _mix(c0, c1, V)
    e = np.abs(2 * U - 1) ** 1.6
    col = col * (1 - (1 - edge_dark) * e)[..., None]
    ridge = np.exp(-((U - 0.5) * 10) ** 2) * 0.22
    return np.clip(_mix(col, (230, 244, 255), ridge), 0, 255)


def shooter_atlas():
    at = Atlas((30, 60, 140))
    at.add("petal", (2, 2, 62, 66), lambda w, h: crystal_art(w, h, (104, 186, 255), (34, 104, 228), edge_dark=0.58))
    at.add("cup", (64, 2, 126, 40), lambda w, h: vgrad(w, h, (16, 44, 120), (34, 92, 210)))
    at.add("gold", (64, 42, 96, 74), lambda w, h: metal_art(w, h, (252, 206, 72), (200, 140, 30)))
    at.add("spike", (98, 42, 126, 74), lambda w, h: vgrad(w, h, (255, 228, 96), (232, 160, 34)))
    at.add("gold_leaf", (2, 68, 62, 126), lambda w, h: petal_art(w, h, (228, 168, 40), (248, 206, 70), (255, 232, 130), edge_dark=0.7, vein=(255, 240, 160)))
    at.add("handle_y", (64, 76, 94, 126), lambda w, h: vgrad(w, h, (252, 214, 76), (224, 172, 42)))
    at.add("handle_g", (96, 76, 126, 112), lambda w, h: vgrad(w, h, (128, 186, 60), (78, 132, 40)))
    at.add("dark", (96, 114, 126, 126), lambda w, h: flat(w, h, (36, 58, 30)))
    return at


def aerial_shooter(p, R):
    """长柄花杖。设计坐标（rig.STAFF）：原点在右手掌心，a 朝前（花头方向），b 朝上，c 朝角色左侧。

    杖身从 a=-22（后端深绿端帽）到 a=+24：后半绿色、前半黄色（粗一点）；接头处一圈金环 + 两片黄叶（萼片）；
    花头：五片折纸感的蓝色花瓣（a 24→39，张开到半径 10）+ 深蓝内杯 + 黄色花蕊圆锥（尖端 a=41 = 枪口）
    + 一圈五根外翻的黄色小尖刺。
    """
    W = p.W
    # 杖身：后半绿（深绿端帽）、前半黄（粗一点）
    p.tube([(-22.0, 0, 0), (-20.2, 0, 0), (-19.8, 0, 0), (3.0, 0, 0)], [2.0, 2.0, 1.6, 1.6], 8, [W] * 4, R["dark"], side_hint=(0, 1, 0), cap_start=True)
    p.tube([(-19.8, 0, 0), (3.0, 0, 0)], [1.61, 1.61], 8, [W] * 2, R["handle_g"], side_hint=(0, 1, 0))
    p.tube([(2.8, 0, 0), (3.8, 0, 0), (23.0, 0, 0)], [1.61, 1.85, 1.85], 8, [W] * 3, R["handle_y"], side_hint=(0, 1, 0))
    # 金环（花托）
    p.tube([(22.0, 0, 0), (23.2, 0, 0), (25.2, 0, 0), (26.0, 0, 0)], [1.9, 2.9, 3.1, 2.5], 9, [W] * 4, R["gold"], side_hint=(0, 1, 0), cap_start=True)
    # 两片黄叶（萼片）：**立起来**，和杖身约 90°、略向花那边倾（用户 2026-09-16：上一版往手柄那边倒，反了）。
    # 一片朝下、一片朝上，正好落在 45° 起步的四片花瓣之间的缝里
    # 叶的宽度沿杖身（宽面朝侧面），从侧面看才是整片叶而不是一条边
    for rad, side in ((norm((0.0, -1.0, 0.15)), 1), (norm((0.0, 1.0, -0.15)), -1)):
        wid = np.array((1.0, 0, 0))
        rows = [(np.array((23.0, 0, 0)) + rad * 2.5, wid, 1.4, W), (np.array((24.2, 0, 0)) + rad * 5.2, wid, 2.6, W), (np.array((25.4, 0, 0)) + rad * 7.2, wid, 2.0, W)]
        p.petal(rows, (np.array((26.8, 0, 0)) + rad * 9.0, W), R["gold_leaf"], hint=(0, 0, side), bulge=0.4)
    # 深蓝内杯（法线朝内）：跟着花苞先张后收，从花瓣缝里看进去是深蓝的
    p.tube([(25.0, 0, 0), (29.0, 0, 0), (33.5, 0, 0), (37.5, 0, 0), (40.5, 0, 0)], [2.0, 5.6, 7.4, 6.0, 3.6], 8, [W] * 5, R["cup"], side_hint=(0, 1, 0), inside=True)
    p.disc((25.0, 0, 0), (1, 0, 0), 2.0, 8, R["cup"], W)
    # ★ 四片蓝色花瓣，**半开的花苞**：先向外鼓、再向前收拢（尖端收到半径 3.4），每片只有 4 排 + 尖，
    #   bulge 顶出一道棱 ⇒ 两个平面的折面，看起来既像花又像水晶（用户 2026-09-16 的描述）。45° 起步，侧面看正好两片
    for k in range(4):
        th = 2 * math.pi * k / 4 + math.pi / 4
        rad = np.array((0.0, math.cos(th), math.sin(th)))
        tan = np.array((0.0, -math.sin(th), math.cos(th)))
        # 菱形轮廓：根窄、中段最宽最鼓、往前收成尖；只有 3 排 + 尖 ⇒ 大平面 + 硬棱，水晶感
        rows = [(np.array((24.5, 0, 0)) + rad * 2.8, tan, 1.8, W),
                (np.array((30.5, 0, 0)) + rad * 9.4, tan, 5.4, W),
                (np.array((37.5, 0, 0)) + rad * 8.0, tan, 3.8, W)]
        p.petal(rows, (np.array((42.5, 0, 0)) + rad * 3.2, W), R["petal"], hint=rad, bulge=2.6, edge_tilt=0.6)
    # 花蕊：一颗黄色圆球坐在花苞口上（前半露在外面），前面凸出**一根**长尖（尖端 a=50 = rig.STAFF.muzzle_a）
    p.sphere((39.0, 0, 0), 3.4, 6, 10, R["spike"], W, axis=(1, 0, 0))
    p.tube([(41.0, 0, 0), (44.0, 0, 0), (50.0, 0, 0)], [1.5, 1.1, 0.0], 7, [W] * 3, R["spike"], side_hint=(0, 1, 0), cap_start=True)


def build_W0003(mtn):
    import rig
    tpl = mshtool.load(os.path.join(CH01, "ch01W0003.msh"))
    at = shooter_atlas()
    p = Part(tpl, "ch03W0003.dds", BoneBind(tpl, mtn))
    o, f, u = rig.frame_local(rig.STAFF)
    p.place(rig.STAFF["bone"], o, f, u)
    aerial_shooter(p, at.regs)
    return tpl, p, at


def potion_atlas():
    at = Atlas((60, 130, 220))
    at.add("glass", (2, 2, 66, 66), lambda w, h: glass_art(w, h, (54, 132, 232), (118, 196, 255)))
    at.add("gold", (68, 2, 100, 34), lambda w, h: metal_art(w, h, (250, 200, 70), (196, 136, 30)))
    at.add("cork", (102, 2, 126, 26), lambda w, h: vgrad(w, h, (118, 160, 64), (84, 126, 46)))
    at.add("leaf", (68, 36, 126, 94), lambda w, h: petal_art(w, h, (142, 200, 44), (110, 176, 32), (170, 220, 70), edge_dark=0.7, vein=(200, 236, 110)))
    at.add("base", (2, 68, 66, 100), lambda w, h: metal_art(w, h, (236, 186, 60), (170, 118, 26)))
    at.add("dark", (68, 96, 126, 126), lambda w, h: flat(w, h, (42, 36, 30)))
    return at


def potion(p, R):
    """药水瓶 / 回血图腾。设计坐标：a 向上，球心 a=0。"""
    W = p.W
    p.sphere((0, 0, 0), 8.2, 7, 12, R["glass"], W, axis=(1, 0, 0), squash=0.9)
    p.tube([(6.5, 0, 0), (9.5, 0, 0), (13.0, 0, 0)], [3.6, 3.0, 2.9], 9, [W] * 3, R["glass"], side_hint=(0, 1, 0))
    p.tube([(12.8, 0, 0), (13.4, 0, 0), (14.8, 0, 0), (15.4, 0, 0)], [3.0, 3.9, 3.9, 3.0], 9, [W] * 4, R["gold"], side_hint=(0, 1, 0))
    p.tube([(15.2, 0, 0), (17.6, 0, 0)], [2.5, 2.7], 8, [W] * 2, R["cork"], side_hint=(0, 1, 0))
    p.disc((17.6, 0, 0), (1, 0, 0), 2.7, 8, R["cork"], W)
    p.petal([((17.5, 0.5, 0), (0, 0, 1), 0.9, W), ((19.5, 2.2, 0), (0, 0, 1), 1.7, W)],
            ((22.0, 4.5, 0), W), R["leaf"], hint=(1, 0, 0), bulge=0.3)
    # 金腰带
    p.tube([(-0.6, 0, 0), (1.6, 0, 0)], [8.45, 8.45], 12, [W] * 2, R["gold"], side_hint=(0, 1, 0))
    # 金底座（能立住）
    p.tube([(-11.5, 0, 0), (-10.0, 0, 0), (-7.0, 0, 0)], [5.2, 5.8, 4.0], 10, [W] * 3, R["base"], side_hint=(0, 1, 0), cap_start=True)


def build_D0003A(mtn):
    tpl = mshtool.load(os.path.join(CH01, "ch01D0003.msh"))
    at = potion_atlas()
    p = Part(tpl, "ch03D0003A.dds", BoneBind(tpl, mtn))
    p.place("Bone_C01W03_01", (0, -2, 0), (0, 1, 0), (0, 0, 1))
    potion(p, at.regs)
    return tpl, p, at


# (名字, 构造器, 母本贴图, 参考帧, 对照图上的说明)。★ D0002 / D0003A 是**弹体**，不是手持件的另一个角度：
# D0002 = 2 号扔出去的种球（和手持同款），D0003A = 3 号发射出去的回血药水图腾（原版没发它的模型，也没有图标，
# 左边那格只是借 3 号武器的图标当行标签）。
BUILDERS = [
    ("W0001", build_W0001, "ch01W0001.dds", 0, "hand-held rifle"),
    ("W0002", build_W0002, "ch01W0002.dds", 1, "hand-held seed bomb"),
    ("W0003", build_W0003, "ch01W0003.dds", 2, "hand-held flower staff"),
    ("D0002", build_D0002, "ch01D0002.dds", 1, "PROJECTILE thrown by W0002 (same seed bomb)"),
    ("D0003A", build_D0003A, "ch01D0003.dds", 2, "PROJECTILE/TOTEM fired by W0003 = healing potion (no icon; left = W0003 icon)"),
]


# ---------------------------------------------------------------------------
# 校验 + 渲染
# ---------------------------------------------------------------------------

def verify(tpl, p, m, mtn):
    blob = mshtool.write(m)
    chk = mshtool.parse(blob, m.path)
    assert mshtool.write(chk) == blob, "自 round-trip 不一致"
    assert chk.head == tpl.head and chk.effects == tpl.effects, "头 / 挂接特效没抄对"
    tpl_off = mshtool.bone_offsets(tpl)
    cnt = np.zeros(chk.nv, int)
    wsum = np.zeros(chk.nv)
    for name, ids, ws in mshtool.bone_table(chk):
        # 骨骼名必须是母本文件骨骼的子集：角色骨里的按名字查骨架，弹体骨是对象自己的帧（不在 .mtn 树里）
        assert name in tpl_off, "骨骼 %s 不在母本文件里" % name
        assert name in set(mtn.names) or name.startswith("Bone_C01W"), "骨骼 %s 既不在骨架里也不是弹体骨" % name
        assert np.array_equal(mshtool.bone_offsets(chk)[name], tpl_off[name]), "骨 %s 的 offset 变了" % name
        cnt[ids] += 1
        wsum[ids] += ws
    assert cnt.min() == 1 and cnt.max() == 1 and abs(wsum - 1).max() < 1e-6, "权重不对"
    pos = chk.verts["pos"].astype(np.float64)
    nrm = chk.verts["nrm"].astype(np.float64)
    f = chk.faces
    fn = np.cross(pos[f[:, 1]] - pos[f[:, 0]], pos[f[:, 2]] - pos[f[:, 0]])
    agree = float(np.mean(np.einsum("ij,ij->i", fn, nrm[f[:, 0]] + nrm[f[:, 1]] + nrm[f[:, 2]]) > 0))
    assert agree >= 0.97, "面绕向和法线不一致：%.3f" % agree
    assert np.isfinite(pos).all() and np.isfinite(nrm).all()
    assert np.abs(np.linalg.norm(nrm, axis=1) - 1).max() < 1e-3, "有零法线"
    return blob, chk, agree


def labeled(img, text, width, height):
    canvas = Image.new("RGB", (width, height), (30, 30, 34))
    scale = min((width - 8) / img.width, (height - 28) / img.height)
    im = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                    Image.NEAREST if scale > 1.5 else Image.LANCZOS)
    canvas.paste(im, ((width - im.width) // 2, 24 + (height - 24 - im.height) // 2))
    ImageDraw.Draw(canvas).text((6, 6), text, fill=(230, 230, 230))
    return canvas


def hcat(panels):
    sheet = Image.new("RGB", (sum(p.width for p in panels), max(p.height for p in panels)), (30, 30, 34))
    x = 0
    for p in panels:
        sheet.paste(p, (x, 0))
        x += p.width
    return sheet


def vcat(rows):
    sheet = Image.new("RGB", (max(r.width for r in rows), sum(r.height for r in rows)), (30, 30, 34))
    y = 0
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height
    return sheet


def ref_frame(i):
    im = Image.open(REF_PNG).convert("RGBA")
    bg = Image.new("RGBA", im.size, (40, 40, 48, 255))
    bg.alpha_composite(im)
    return bg.convert("RGB").crop(REF_RECTS[i])


def render_sheet(results):
    """每件一行：参考帧 | 卡希尔原件（正/侧/顶）| 爱琳新件（同视角同比例）。"""
    size = 260
    rows = []
    for name, tpl, m, rgba, ref_i, note in results:
        tpl_tex = dds_edit.load_rgba(os.path.join(CH01, tpl.textures[0]))[1]
        panels = [labeled(ref_frame(ref_i), "reference Ch003Weapons f%d" % ref_i, size, size + 24)]
        # ★ 卡希尔那格是**模板文件**（抄头 / 骨骼 offset 用的），不一定是她实机发射的东西：
        #   [ch01-03] 캐논왈츠 用的是 2D 精灵 `Anim,CH01_WP3`（火箭弹），ch01D0003.msh 那个宝箱在 weapon.ini 里没人引用
        tpl_label = "template %s" % tpl.path.split(os.sep)[-1]
        if name == "D0003A":
            tpl_label += " (NOT what Casil fires: [ch01-03] uses 2D sprite CH01_WP3; this .msh is unreferenced)"
        elif name.startswith("D"):
            tpl_label += " (Casil's projectile, same as her hand-held)"
        else:
            tpl_label += " (Casil's hand-held, before)"
        for label, mesh, tex in ((tpl_label, tpl, tpl_tex),
                                 ("Irene ch03%s (new) = %s" % (name, note), m, rgba)):
            # 各按自己的包围盒放大（卡希尔的 W0001 是左右两把、跨 ±418 网格单位，共用包围盒会把新件缩成一粒）
            row = Image.new("RGB", (size * 3, size))
            for i, view in enumerate(("front", "side", "top")):
                row.paste(Image.fromarray(mshtool.rasterize([(mesh, tex)], view=view, size=size)), (i * size, 0))
            panels.append(labeled(row, "%s  [%d verts / %d faces]" % (label, mesh.nv, mesh.ni // 3), size * 3, size + 24))
        panels.append(labeled(Image.fromarray(rgba[..., :3]), "ch03%s.dds" % name, size, size + 24))
        rows.append(hcat(panels))
    out = os.path.join(HERE, "compare_E_weapons.png")
    vcat(rows).save(out)
    print("->", out)


def render_inhand(results):
    """手持三件：和爱琳当前的身体件一起蒙皮到 Stand00 / Attack0N 的几帧。"""
    size = 360
    body = geometry.part_meshes(["ch0300000", "ch03H0000", "ch03B0000", "ch03G0000", "ch03L0000", "ch03S0000"])
    bbox = (np.array([-17.0, -0.5, -17.0]), np.array([17.0, 40.0, 17.0]))
    # 持第 N 把武器时站姿是 StandN（`Stand%02d`，0x6858f8），不是 Stand00；步枪的三帧用移植后的动作（anim.py 已写进 ch03）
    frames = {"W0001": (("Stand01", 0), ("Attack01", 2), ("Reload01", 26)),
              "W0002": (("Stand02", 0), ("Attack02", 12), ("Attack02", 30)),
              "W0003": (("Stand03", 0), ("Attack03", 4), ("Attack03", 12))}
    rows = []
    for name, tpl, m, rgba, _, _ in results:
        if name not in frames:
            continue
        parts = body + [(m, rgba)]
        panels = []
        for anim, frame in frames[name]:
            mtn = mtntool.parse(os.path.join(CH03, "ch03@%s.mtn" % anim))
            t = min(frame * mtntool.TICKS_PER_FRAME, mtn.duration * mtn.ticks_per_sec)
            pp = geometry.posed(parts, mtn, t)
            row = Image.new("RGB", (size * 2, size))
            for i, view in enumerate(("front", "rot35")):
                row.paste(Image.fromarray(mshtool.rasterize(pp, view=view, size=size, bbox=bbox)), (i * size, 0))
            panels.append(labeled(row, "ch03%s · %s f%d · front / rot35" % (name, anim, frame), size * 2, size + 24))
        rows.append(hcat(panels))
    out = os.path.join(HERE, "compare_E_inhand.png")
    vcat(rows).save(out)
    print("->", out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=[b[0] for b in BUILDERS])
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args(argv)

    mtn = mtntool.parse(os.path.join(CH03, "ch03@Stand00.mtn"))
    results = []
    for name, fn, tpl_dds, ref_i, note in BUILDERS:
        if args.only and args.only != name:
            continue
        tpl, p, at = fn(mtn)
        m = p.finish()
        m.path = os.path.join(CH03, "ch03%s.msh" % name)
        blob, chk, agree = verify(tpl, p, m, mtn)
        rgba = at.rgba()
        print("ch03%s: 顶点 %d -> %d，面 %d -> %d，骨 %s；绕向一致率 %.1f%%；%d B"
              % (name, tpl.nv, chk.nv, tpl.ni // 3, chk.ni // 3, [n for n, _ in chk.bones], agree * 100, len(blob)))
        results.append((name, tpl, chk, rgba, ref_i, note))
        if not args.dry_run:
            w1 = _write_if_changed(m.path, blob)
            w2 = write_dds(os.path.join(CH01, tpl_dds), rgba, os.path.join(CH03, "ch03%s.dds" % name))
            print("   写出：.msh %s，.dds %s" % ("已更新" if w1 else "未变", "已更新" if w2 else "未变"))
    if not args.no_render:
        render_sheet(results)
        render_inhand(results)
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
