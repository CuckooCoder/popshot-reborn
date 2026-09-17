#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mshtool.py —— 角色 `.msh` 网格的解析 / 写出 / 离线渲染 / 顶点位移（X_Mod · X1 爱琳 M7）。

    python tools/mshtool.py roundtrip DIR [DIR ...]      # ★ 准入门槛：全目录 parse->write 逐字节一致
    python tools/mshtool.py info   a.msh [b.msh ...]     # 顶点/索引/材质/骨骼概况
    python tools/mshtool.py uv     a.msh tex.png out.png # 把 UV 线框画在贴图上（看哪块贴图给了哪些三角形）
    python tools/mshtool.py render a.msh out.png [--tex tex.png] [--views front,side,top]
                                                         # 正交线框 + 顶点点云，多视角拼一张
    python tools/mshtool.py displace a.msh spec.json out.msh   # 按 spec 移动顶点（只动位置，见下）

## 格式（FINDINGS §7 + 本文件实测补全，✅ 在 ch03 全部 257 个文件上逐字节 round-trip）

```
0x00  u32 版本 = 3
0x04  16 × f32 世界矩阵
0x44  4 字节标志（绝大多数 01 01 00 00；ch03H0000 是 02 02 78 00）—— 原样保留
0x48  u32 挂接特效数 n（0 或 1）
      每条：u32 码元数 + UTF-16LE 路径（`ArmorSpakle\\Efx\\CH01_*.efx`）+ 4 字节
      然后 32 字节全零块
      u32 顶点数 nv，u32 索引数 ni
      nv × 32 B 顶点（f32 x y z | f32 nx ny nz | f32 u v）
      ni × u16 索引（三角形列表，ni = 3 × 面数）
      u32 材质数 nm（1 或 2；ch03 里 45 个头发件是 2）
        每个：17 × f32（D3DMATERIAL9：diffuse/ambient/specular/emissive 各 4 + power）
              + u32 贴图数 + 每张 u32 码元数 + UTF-16LE 文件名
      u32 子集数（= nm），每个 4 × u32（顶点起, 顶点数, 面起, 面数）
      u32 骨骼数，每根：u32 码元数 + UTF-16LE 骨骼名 + u32 n + n × u32 顶点号 + n × f32 权重 + 16 × f32
      u32 面数，每面 40 B 邻接表（u32 面号 + u32 邻居数 + 3 × u16 邻居面 + 3 × 2 × u16 共享边 + 14 B 零）
      [尾巴] 极少数文件（ch0300011）邻接表后面还挂着 676 B 的**残片**（另一张更长邻接表的中段，
             面号对不上、也不对齐）—— 是导出工具复用缓冲区留下的垃圾，引擎只读 n 面就停。
             按不透明字节原样保留。
```

最后那张邻接表是给模板阴影（stencil shadow volume）用的，**只依赖索引拓扑**。
`displace` 只改顶点的 xyz，不动索引、不动骨骼、不动邻接 ⇒ 表仍然成立。

## displace 的铁律

- **只写位置（xyz），法线原样、UV 原样、骨骼块逐字节原样。**
  引擎按名字在角色骨架里找骨骼，找不到返 NULL 喂进矩阵乘直接崩（V0.3商店 §50）。
- 同一位置上的多个顶点（UV 缝、法线缝）**必须一起动**，否则网格开裂。
  ⇒ spec 里选顶点用 `"weld"`（按位置合并）而不是裸顶点号。
- 写完先用自己的 parser 再读一遍，顶点数 / 索引数 / 骨骼块 / 邻接表和改前逐字节一致才算数。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys

import numpy as np

VERTEX_DTYPE = np.dtype([("pos", "<f4", 3), ("nrm", "<f4", 3), ("uv", "<f4", 2)])
assert VERTEX_DTYPE.itemsize == 32
ADJ_ENTRY = 40


class Mesh:
    """解析结果。除 `verts` 之外的字段都是**原始字节**，写回时原样拼接。"""

    __slots__ = ("head", "effects", "pad", "verts", "indices", "materials",
                 "textures", "subsets", "bones", "adjacency", "tail", "path")

    def __init__(self):
        self.head = b""        # [0:0x48]：版本 + 矩阵 + 4 字节标志
        self.effects = []      # [(u32 前缀 + 路径 + 4 字节) 原始字节]
        self.pad = b""         # 32 字节零块
        self.verts = None      # ndarray(VERTEX_DTYPE)，nv 个
        self.indices = None    # ndarray(<u2)，ni 个
        self.materials = b""   # 材质块 + 子集表的原始字节（含贴图名）
        self.textures = []     # 材质块里解析出来的贴图名（只读，供 info 用）
        self.subsets = []      # [(顶点起, 顶点数, 面起, 面数)]（只读）
        self.bones = []        # [(名字, 原始字节)]
        self.adjacency = b""   # 邻接表原始字节（含前面的 u32 面数）
        self.tail = b""        # 邻接表之后的残片（通常空）
        self.path = ""

    # -- 便利属性 ------------------------------------------------------------
    @property
    def nv(self):
        return len(self.verts)

    @property
    def ni(self):
        return len(self.indices)

    @property
    def faces(self):
        return self.indices.reshape(-1, 3)


def _u32(b, off):
    return struct.unpack_from("<I", b, off)[0]


def _read_wstr(b, off):
    """u32 码元数 + UTF-16LE。返回 (文本, 新偏移)。"""
    n = _u32(b, off)
    if n > 1024:
        raise ValueError("字符串长度 %d 不合理 @%#x" % (n, off))
    s = b[off + 4: off + 4 + 2 * n].decode("utf-16le")
    return s, off + 4 + 2 * n


def parse(blob, path=""):
    m = Mesh()
    m.path = path
    if len(blob) < 0x50 or _u32(blob, 0) != 3:
        raise ValueError("版本不是 3 或文件太短")
    m.head = blob[:0x48]
    off = 0x48
    n_eff = _u32(blob, off)
    off += 4
    if n_eff > 8:
        raise ValueError("挂接特效数 %d 不合理" % n_eff)
    for _ in range(n_eff):
        start = off
        _, off = _read_wstr(blob, off)
        off += 4
        m.effects.append(blob[start:off])
    m.pad = blob[off:off + 32]
    if m.pad != bytes(32):
        raise ValueError("特效串之后的 32 字节不是全零 @%#x：%s" % (off, m.pad.hex()))
    off += 32
    nv, ni = struct.unpack_from("<II", blob, off)
    off += 8
    if nv == 0 or nv > 65535 or ni == 0 or ni % 3:
        raise ValueError("nv=%d ni=%d 不合理" % (nv, ni))
    m.verts = np.frombuffer(blob, dtype=VERTEX_DTYPE, count=nv, offset=off).copy()
    off += nv * 32
    m.indices = np.frombuffer(blob, dtype="<u2", count=ni, offset=off).copy()
    off += ni * 2
    if int(m.indices.max()) >= nv:
        raise ValueError("索引越界")

    # 材质块：结构化地走一遍以确定长度，但整块按原始字节保留
    mat_start = off
    n_mat = _u32(blob, off)
    off += 4
    if not 1 <= n_mat <= 8:
        raise ValueError("材质数 %d 不合理" % n_mat)
    for _ in range(n_mat):
        off += 17 * 4
        n_tex = _u32(blob, off)
        off += 4
        if n_tex > 8:
            raise ValueError("贴图数 %d 不合理" % n_tex)
        for _ in range(n_tex):
            name, off = _read_wstr(blob, off)
            m.textures.append(name)
    n_sub = _u32(blob, off)
    off += 4
    if n_sub != n_mat:
        raise ValueError("子集数 %d != 材质数 %d" % (n_sub, n_mat))
    for _ in range(n_sub):
        sub = struct.unpack_from("<4I", blob, off)
        off += 16
        if sub[0] + sub[1] > nv or (sub[2] + sub[3]) * 3 > ni:
            raise ValueError("子集 %r 超出 nv=%d/ni=%d" % (sub, nv, ni))
        m.subsets.append(sub)
    if sum(s[3] for s in m.subsets) * 3 != ni:
        raise ValueError("子集面数之和 != ni/3")
    m.materials = blob[mat_start:off]

    n_bone = _u32(blob, off)
    off += 4
    if n_bone > 256:
        raise ValueError("骨骼数 %d 不合理" % n_bone)
    for _ in range(n_bone):
        start = off
        name, off = _read_wstr(blob, off)
        n = _u32(blob, off)
        off += 4 + n * 8 + 64
        m.bones.append((name, blob[start:off]))

    n_face = _u32(blob, off)
    if n_face * 3 != ni:
        raise ValueError("邻接表面数 %d != ni/3=%d" % (n_face, ni // 3))
    end = off + 4 + n_face * ADJ_ENTRY
    if end > len(blob):
        raise ValueError("邻接表结束 %d 超过文件长度 %d" % (end, len(blob)))
    # 邻接表的面号必须是 0..n-1 顺序排好的，否则说明前面的解析已经跑偏
    ids = np.frombuffer(blob, dtype="<u4", count=n_face * (ADJ_ENTRY // 4), offset=off + 4)
    if not np.array_equal(ids[::ADJ_ENTRY // 4], np.arange(n_face, dtype="<u4")):
        raise ValueError("邻接表面号不是 0..%d 顺序" % (n_face - 1))
    m.adjacency = blob[off:end]
    m.tail = blob[end:]
    return m


def write(m):
    parts = [m.head, struct.pack("<I", len(m.effects))]
    parts.extend(m.effects)
    parts.append(m.pad)
    parts.append(struct.pack("<II", m.nv, m.ni))
    parts.append(np.ascontiguousarray(m.verts, dtype=VERTEX_DTYPE).tobytes())
    parts.append(np.ascontiguousarray(m.indices, dtype="<u2").tobytes())
    parts.append(m.materials)
    parts.append(struct.pack("<I", len(m.bones)))
    parts.extend(raw for _, raw in m.bones)
    parts.append(m.adjacency)
    parts.append(m.tail)
    return b"".join(parts)


def load(path):
    return parse(open(path, "rb").read(), path)


# ---------------------------------------------------------------------------
# 骨骼权重（只读）
# ---------------------------------------------------------------------------

def bone_table(m):
    """[(骨骼名, 顶点号 ndarray, 权重 ndarray)]。只解析、不改。"""
    out = []
    for name, raw in m.bones:
        off = 4 + 2 * len(name)
        n = _u32(raw, off)
        off += 4
        ids = np.frombuffer(raw, dtype="<u4", count=n, offset=off)
        ws = np.frombuffer(raw, dtype="<f4", count=n, offset=off + 4 * n)
        out.append((name, ids, ws))
    return out


def bone_offsets(m):
    """dict 骨骼名 -> 4x4 offset 矩阵（绑定姿态世界矩阵的逆，网格单位，见 mtntool.py 的单位说明）。"""
    out = {}
    for name, raw in m.bones:
        off = 4 + 2 * len(name)
        n = _u32(raw, off)
        off += 4 + 8 * n
        out[name] = np.frombuffer(raw, dtype="<f4", count=16, offset=off).reshape(4, 4).astype(np.float64)
    return out


def vertex_weights(m):
    """每个顶点的 [(骨骼名, 权重)] 列表。"""
    out = [[] for _ in range(m.nv)]
    for name, ids, ws in bone_table(m):
        for i, w in zip(ids.tolist(), ws.tolist()):
            out[i].append((name, w))
    return out


def skin_positions(m, worlds, scale=1.0):
    """按 `worlds`（骨骼名 -> 4x4 世界矩阵，场景单位，来自 mtntool.world_mats）把顶点蒙皮到某一帧。

    v' = Σ w · v · offset · world。返回 (nv, 3) 的场景单位坐标 × scale
    （传 1/2.8791 就回到网格单位，好和未蒙皮的部件放在同一张图里比较）。
    """
    offs = bone_offsets(m)
    pos = np.concatenate([m.verts["pos"].astype(np.float64), np.ones((m.nv, 1))], axis=1)
    out = np.zeros((m.nv, 3))
    for name, ids, ws in bone_table(m):
        M = offs[name] @ worlds[name]
        p = pos[ids] @ M
        out[ids] += ws[:, None] * p[:, :3]
    return out * scale


# ---------------------------------------------------------------------------
# 写出新内容用的块构造器（追加几何时用；只改顶点位置的 displace 不需要这些）
# ---------------------------------------------------------------------------

def _material_floats(m):
    return m.materials[4:4 + 17 * 4]


def build_bone_block(bones):
    """`bones`：[(名字, 顶点号 ndarray, 权重 ndarray, 4x4 offset)] -> [(名字, 原始字节)]。"""
    out = []
    for name, ids, ws, off in bones:
        ids = np.asarray(ids, dtype="<u4")
        ws = np.asarray(ws, dtype="<f4")
        assert len(ids) == len(ws) and len(ids) > 0, name
        raw = (struct.pack("<I", len(name)) + name.encode("utf-16le") + struct.pack("<I", len(ids))
               + ids.tobytes() + ws.tobytes() + np.asarray(off, dtype="<f4").reshape(16).tobytes())
        out.append((name, raw))
    return out


def build_adjacency(positions, faces):
    """模板阴影邻接表（✅ 在 ch01 256 个文件里逐字节复现 **235** 个，含全部 6 个默认部件）。

    规则：按**位置逐位相等**焊接顶点后找共边；每条共边上的面**按面号排序后两两配对**
    `(f0,f1), (f2,f3), …`，每对**只记在面号小的那一面**里
    （`u32 面号, u32 邻居数, 3×u16 邻居面(补 0xFFFF), 3×2×u16 本面这条边的两个顶点号(补 0), 14 B 零`），
    邻居按本面的 (a,b),(b,c),(c,a) 边序排列，同一个邻居在一条记录里只出现一次。
    剩下 21 个文件差一两条边（导出工具的焊接容差不同），只影响阴影，不影响渲染。

    ★ 2026-09-15 改过一次规则，这是爱琳「裙子上有网格线」的根因（FINDINGS §17）：
    旧规则是「每条边取**第一个面号更大的面**当邻居」。单层网格两种规则等价，但 `geometry.py`
    的双面薄片（`mirror_faces`：正面 F/F2 + 反绕向副本 F'/F2'）在**每条内部边**上有 4 个面，
    旧规则会写出 `F2→F'`（正面配反面）—— 而这张表是给模板阴影抽轮廓边用的：一正一反必然
    「一面朝光一面背光」，于是花瓣的**每条内部边都被当成轮廓边挤出阴影体**，在裙子上画出
    一格一格的暗线。原版 `ch01B0006`（蝴蝶结，15 组反绕向重复面）的表证明导出工具用的是
    两两配对：内部边记 `(F,F2)` + `(F',F2')`，只有**边界边**才记 `(F,F')`。
    """
    faces = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    key_of = {}
    g = np.empty(len(positions), dtype=np.int64)
    for i, p in enumerate(map(tuple, np.asarray(positions, dtype="<f4"))):
        g[i] = key_of.setdefault(p, len(key_of))
    edge_map = {}
    for fi, (a, b, c) in enumerate(faces):
        for u, v in ((a, b), (b, c), (c, a)):
            edge_map.setdefault(frozenset((int(g[u]), int(g[v]))), []).append(fi)
    partner = {}
    for key, fl in edge_map.items():
        fl = sorted(set(fl))
        for i in range(0, len(fl) - 1, 2):
            partner[(key, fl[i])] = fl[i + 1]
    out = [struct.pack("<I", len(faces))]
    for fi, (a, b, c) in enumerate(faces):
        nbs = []
        for u, v in ((a, b), (b, c), (c, a)):
            nb = partner.get((frozenset((int(g[u]), int(g[v]))), fi))
            if nb is None or any(n == nb for n, _, _ in nbs):
                continue
            nbs.append((nb, int(u), int(v)))
        nb = [n for n, _, _ in nbs] + [0xFFFF] * (3 - len(nbs))
        ed = [x for _, u, v in nbs for x in (u, v)] + [0] * (6 - 2 * len(nbs))
        out.append(struct.pack("<II", fi, len(nbs)) + struct.pack("<3H", *nb) + struct.pack("<6H", *ed) + bytes(14))
    return b"".join(out)


def adjacency_pairs(m):
    """把邻接表解成 [(面号, 邻居面号, 边顶点 u, 边顶点 v)]。只读。"""
    out = []
    nf = m.ni // 3
    for fi in range(nf):
        rec = m.adjacency[4 + fi * ADJ_ENTRY: 4 + (fi + 1) * ADJ_ENTRY]
        n = struct.unpack_from("<I", rec, 4)[0]
        nb = struct.unpack_from("<3H", rec, 8)
        ed = struct.unpack_from("<6H", rec, 14)
        for j in range(n):
            out.append((fi, nb[j], ed[2 * j], ed[2 * j + 1]))
    return out


def silhouette_edges(m, light=(0.3, 0.5, 0.8), pairs=None):
    """按邻接表模拟模板阴影的轮廓边抽取：一对邻居**一面朝光、一面背光**的那条边。

    返回 [(u, v)]。这就是引擎会拿去挤出阴影体的边 —— 表配错了（正面配反面）的话，
    双面薄片的每条内部边都会出现在这里。渲染时把它们画在贴图渲染上，就是「实机会看到的暗线」。
    """
    L = np.asarray(light, dtype=np.float64)
    L /= np.linalg.norm(L)
    pos = m.verts["pos"].astype(np.float64)
    f = m.faces
    fn = np.cross(pos[f[:, 1]] - pos[f[:, 0]], pos[f[:, 2]] - pos[f[:, 0]])
    lit = fn @ L > 0
    return [(u, v) for fi, nb, u, v in (pairs or adjacency_pairs(m)) if lit[fi] != lit[nb]]


def assemble(template, verts, faces, texture, bones, effects=None):
    """用 `template`（一个已解析的 Mesh，抄它的头 / 标志 / 材质 float）拼一个新 Mesh 并返回。

    verts：VERTEX_DTYPE 数组；faces：(n,3) 索引；texture：单材质的贴图名；
    bones：[(名字, 顶点号, 权重, offset 4x4)]。单材质、单子集。
    """
    m = Mesh()
    m.path = template.path
    m.head = template.head
    m.effects = list(template.effects if effects is None else effects)
    m.pad = bytes(32)
    m.verts = np.ascontiguousarray(verts, dtype=VERTEX_DTYPE)
    faces = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    if faces.max() >= len(m.verts) or len(m.verts) > 65535:
        raise ValueError("索引越界或顶点数超过 u16")
    m.indices = faces.reshape(-1).astype("<u2")
    mat = struct.pack("<I", 1) + _material_floats(template) + struct.pack("<I", 1)
    mat += struct.pack("<I", len(texture)) + texture.encode("utf-16le")
    mat += struct.pack("<I", 1) + struct.pack("<4I", 0, len(m.verts), 0, len(faces))
    m.materials = mat
    m.textures = [texture]
    m.subsets = [(0, len(m.verts), 0, len(faces))]
    m.bones = build_bone_block(bones)
    m.adjacency = build_adjacency(m.verts["pos"], faces)
    m.tail = b""
    return m


def dominant_bone(m):
    """每个顶点权重最大的骨骼下标（-1 = 没被任何骨骼引用）。"""
    best_w = np.full(m.nv, -1.0)
    best = np.full(m.nv, -1, dtype=np.int64)
    for bi, (_, ids, ws) in enumerate(bone_table(m)):
        ids = ids.astype(np.int64)
        better = ws > best_w[ids]
        best[ids[better]] = bi
        best_w[ids[better]] = ws[better]
    return best


def uv_zone_mask(m, tw, th, bone_pred, grow=0):
    """把「三个顶点里多数顶点的主骨骼满足 bone_pred」的三角形在 UV 空间光栅化成 (th, tw) 的 bool 掩码。

    贴图上的每个纹素归到「画它的那块网格」—— 比按颜色猜靠谱得多（皮肤和米白布的色相只差 5 度）。
    `grow`：向外膨胀几个纹素，盖住双线性采样时会摸到的边缘。
    """
    names = [n for n, _ in m.bones]
    dom = dominant_bone(m)
    ok_bone = np.array([bone_pred(n) for n in names] + [False])  # -1 -> False
    vert_ok = ok_bone[dom]
    mask = np.zeros((th, tw), dtype=bool)
    uv = m.verts["uv"].astype(np.float64)
    for tri in m.faces:
        if vert_ok[tri].sum() < 2:
            continue
        xs = uv[tri, 0] * tw
        ys = uv[tri, 1] * th
        x0, x1 = int(max(np.floor(xs.min()), 0)), int(min(np.ceil(xs.max()), tw - 1))
        y0, y1 = int(max(np.floor(ys.min()), 0)), int(min(np.ceil(ys.max()), th - 1))
        det = (xs[1] - xs[0]) * (ys[2] - ys[0]) - (xs[2] - xs[0]) * (ys[1] - ys[0])
        if abs(det) < 1e-12 or x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        w1 = ((gx - xs[0]) * (ys[2] - ys[0]) - (gy - ys[0]) * (xs[2] - xs[0])) / det
        w2 = ((gy - ys[0]) * (xs[1] - xs[0]) - (gx - xs[0]) * (ys[1] - ys[0])) / det
        w0 = 1 - w1 - w2
        eps = -0.02  # 略微放宽，纹素中心刚好压在边上的也算
        inside = (w0 >= eps) & (w1 >= eps) & (w2 >= eps)
        mask[y0:y1 + 1, x0:x1 + 1] |= inside
    for _ in range(grow):
        p = np.pad(mask, 1)
        mask = (p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:] | mask)
    return mask


# ---------------------------------------------------------------------------
# 焊接（按位置合并顶点）
# ---------------------------------------------------------------------------

def weld_groups(m, eps=1e-4):
    """返回 `顶点号 -> 焊接组号`，位置相同（误差 eps 内）的顶点同组。"""
    pos = m.verts["pos"].astype(np.float64)
    key = np.round(pos / eps).astype(np.int64)
    groups = {}
    out = np.empty(m.nv, dtype=np.int64)
    for i, k in enumerate(map(tuple, key)):
        out[i] = groups.setdefault(k, len(groups))
    return out


# ---------------------------------------------------------------------------
# 顶点位移
# ---------------------------------------------------------------------------

def select_vertices(m, sel):
    """spec 里的选择器 -> bool 掩码。

    支持的键（全部可组合，取交集）：
      "uv_box":  [u0, v0, u1, v1]   UV 落在矩形内（贴图坐标，0..1）
      "pos_box": [[x0,y0,z0],[x1,y1,z1]]  位置落在盒子内（null 表示不限）
      "ids":     [顶点号, ...]
      "weld":    true  —— 把选中顶点的焊接组整组带上（默认 true）
    """
    mask = np.ones(m.nv, dtype=bool)
    if "uv_box" in sel:
        u0, v0, u1, v1 = sel["uv_box"]
        uv = m.verts["uv"]
        mask &= (uv[:, 0] >= u0) & (uv[:, 0] <= u1) & (uv[:, 1] >= v0) & (uv[:, 1] <= v1)
    if "pos_box" in sel:
        lo, hi = sel["pos_box"]
        pos = m.verts["pos"]
        for axis in range(3):
            if lo[axis] is not None:
                mask &= pos[:, axis] >= lo[axis]
            if hi[axis] is not None:
                mask &= pos[:, axis] <= hi[axis]
    if "ids" in sel:
        ids = np.zeros(m.nv, dtype=bool)
        ids[np.asarray(sel["ids"], dtype=np.int64)] = True
        mask &= ids
    if sel.get("weld", True):
        g = weld_groups(m)
        hit = set(g[mask].tolist())
        mask = np.isin(g, list(hit)) if hit else mask
    return mask


def apply_ops(m, spec):
    """按 spec["ops"] 顺序对顶点位置做变换。返回 (移动了的顶点数, 每步命中数)。

    每步：{"select": {...}, "translate": [dx,dy,dz]}                   平移
          {"select": {...}, "scale": [sx,sy,sz], "pivot": [x,y,z]}     绕枢轴缩放
          {"select": {...}, "pull": {"toward": [x,y,z], "amount": t}}  朝某点拉（t<0 是推开）
          {"select": {...}, "mirror_x": true}  对 x<0 的镜像顶点做同样的事（默认 true）
    所有 op 只改 verts["pos"]。
    """
    pos = m.verts["pos"].astype(np.float64)
    original = pos.copy()
    counts = []
    for op in spec["ops"]:
        mask = select_vertices(m, op["select"])
        if op.get("mirror_x", True) and "pos_box" in op["select"]:
            lo, hi = op["select"]["pos_box"]
            mlo = [None if hi[0] is None else -hi[0], lo[1], lo[2]]
            mhi = [None if lo[0] is None else -lo[0], hi[1], hi[2]]
            sel2 = dict(op["select"], pos_box=[mlo, mhi])
            mask2 = select_vertices(m, sel2)
        else:
            mask2 = np.zeros(m.nv, dtype=bool)
        n_hit = int(mask.sum() + mask2.sum())
        counts.append(n_hit)
        for mk, sign in ((mask, 1.0), (mask2, -1.0)):
            if not mk.any():
                continue
            p = pos[mk]
            if "translate" in op:
                d = np.asarray(op["translate"], dtype=np.float64)
                d[0] *= sign
                p = p + d
            if "scale" in op:
                s = np.asarray(op["scale"], dtype=np.float64)
                piv = np.asarray(op["pivot"], dtype=np.float64)
                piv[0] *= sign
                p = (p - piv) * s + piv
            if "pull" in op:
                tgt = np.asarray(op["pull"]["toward"], dtype=np.float64)
                tgt[0] *= sign
                p = p + (tgt - p) * float(op["pull"]["amount"])
            pos[mk] = p
    moved = int(np.any(np.abs(pos - original) > 1e-9, axis=1).sum())
    m.verts["pos"] = pos.astype("<f4")
    return moved, counts


# ---------------------------------------------------------------------------
# 离线渲染（PIL）
# ---------------------------------------------------------------------------

def _project(pos, view):
    """正交投影到 2D。视图：front (x,y) 看 -z / side (z,y) / top (x,z)。"""
    if view == "front":
        return pos[:, 0], pos[:, 1]
    if view == "back":
        return -pos[:, 0], pos[:, 1]
    if view == "side":
        return pos[:, 2], pos[:, 1]
    if view == "top":
        return pos[:, 0], -pos[:, 2]
    raise ValueError(view)


def render(meshes, out_png, views=("front", "side", "top"), size=480, highlight=None,
           title=None):
    """多个网格叠在一起画线框 + 顶点。`highlight`：bool 掩码列表（每个网格一份），命中的顶点画红点。"""
    from PIL import Image, ImageDraw

    allpos = np.concatenate([m.verts["pos"] for m in meshes]).astype(np.float64)
    lo, hi = allpos.min(axis=0), allpos.max(axis=0)
    span = float((hi - lo).max()) or 1.0
    center = (lo + hi) / 2
    scale = (size - 40) / span

    img = Image.new("RGB", (size * len(views), size + 20), (24, 24, 28))
    draw = ImageDraw.Draw(img)
    palette = [(120, 200, 255), (255, 200, 120), (160, 255, 160), (230, 160, 255)]
    for vi, view in enumerate(views):
        ox = vi * size
        draw.text((ox + 6, 4), view, fill=(200, 200, 200))
        for mi, m in enumerate(meshes):
            pos = m.verts["pos"].astype(np.float64) - center
            px, py = _project(pos, view)
            sx = ox + size / 2 + px * scale
            sy = 20 + size / 2 - py * scale
            col = palette[mi % len(palette)]
            faces = m.faces
            for a, b, c in faces:
                pts = [(sx[a], sy[a]), (sx[b], sy[b]), (sx[c], sy[c]), (sx[a], sy[a])]
                draw.line(pts, fill=col, width=1)
            hl = highlight[mi] if highlight else None
            for i in range(m.nv):
                if hl is not None and hl[i]:
                    draw.ellipse((sx[i] - 2.5, sy[i] - 2.5, sx[i] + 2.5, sy[i] + 2.5), fill=(255, 60, 60))
    if title:
        draw.text((6, size + 4), title, fill=(220, 220, 220))
    img.save(out_png)


def view_matrix(view):
    """视图变换：把 (x,y,z) 变成 (屏幕x, 屏幕y, 深度朝观察者为正) 的 3×3（行向量右乘 R.T）。

    角色的脸朝 -z（✅ 实测：脸只在从 -z 看过去的图里出现），所以 front 是从 -z 看。
    """
    if view == "front":
        return np.array([[-1, 0, 0], [0, 1, 0], [0, 0, -1.0]])
    if view == "back":
        return np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1.0]])
    if view == "side":  # 从 +x 看，脸朝画面左边
        return np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0.0]])
    if view == "top":
        return np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0.0]])
    if view.startswith("rot"):  # 从正面（-z）绕 y 转 N 度
        a = math.radians(180.0 + float(view[3:]))
        return np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    raise ValueError(view)


def project(pos, view, size, bbox):
    """和 rasterize 同一套投影：返回 (视空间坐标 (n,3), 屏幕 x, 屏幕 y)。叠加画线时用它对齐。"""
    lo, hi = (np.asarray(v, dtype=np.float64) for v in bbox)
    span = float((hi - lo).max()) or 1.0
    center = (lo + hi) / 2
    scale = (size - 24) / span
    p = (np.asarray(pos, dtype=np.float64) - center) @ view_matrix(view).T
    return p, size / 2 + p[:, 0] * scale, size / 2 - p[:, 1] * scale


def draw_edges(color, zbuf, m, edges, view, size, bbox, rgb=(20, 20, 20), eps=0.02):
    """把 `edges` [(u, v)] 画到 rasterize 的结果上，**只画没被挡住的部分**（沿线采样 z-buffer）。

    用来把 silhouette_edges() 抽出来的边叠在贴图渲染上，模拟引擎的阴影体会落在哪里。
    """
    p, sx, sy = project(m.verts["pos"], view, size, bbox)
    lo, hi = (np.asarray(v, dtype=np.float64) for v in bbox)
    tol = eps * float((hi - lo).max())
    out = color.copy()
    for u, v in edges:
        n = int(max(abs(sx[u] - sx[v]), abs(sy[u] - sy[v]))) + 1
        t = np.linspace(0, 1, n)
        xs = np.rint(sx[u] + (sx[v] - sx[u]) * t).astype(int)
        ys = np.rint(sy[u] + (sy[v] - sy[u]) * t).astype(int)
        zs = p[u, 2] + (p[v, 2] - p[u, 2]) * t
        ok = (xs >= 0) & (xs < size) & (ys >= 0) & (ys < size)
        xs, ys, zs = xs[ok], ys[ok], zs[ok]
        vis = zs >= zbuf[ys, xs] - tol
        out[ys[vis], xs[vis]] = rgb
    return out


def rasterize(parts, view="front", size=512, light=(0.3, 0.5, 0.8), bg=(56, 56, 64),
              bbox=None, cull=True, return_z=False):
    """带贴图的正交软光栅：z-buffer + 重心插值 UV + 最近邻采样 + 半兰伯特。

    `parts`：[(Mesh, RGBA uint8 ndarray 贴图)]。返回 RGB uint8 ndarray (size, size, 3)
    （`return_z=True` 时返回 (color, zbuf)，给 draw_edges 用）。
    `bbox`：(lo, hi) 世界包围盒，几张图要同一比例时传同一个。
    `cull`：剔除背面（按 cross(b-a, c-a) 的朝向，和引擎一样开着背面剔除 ——
    不剔的话双面薄片的正反两份会 z-fight 成一片麻点）。
    只是给人看的，不追求和引擎一致（引擎有骨骼蒙皮 / 材质 / 雾）。
    """
    allpos = np.concatenate([m.verts["pos"] for m, _ in parts]).astype(np.float64)
    if bbox is None:
        lo, hi = allpos.min(axis=0), allpos.max(axis=0)
    else:
        lo, hi = (np.asarray(v, dtype=np.float64) for v in bbox)

    color = np.empty((size, size, 3), dtype=np.uint8)
    color[:] = bg
    zbuf = np.full((size, size), -np.inf)
    L = np.asarray(light, dtype=np.float64)
    L /= np.linalg.norm(L)
    R = view_matrix(view)

    for m, tex in parts:
        th, tw = tex.shape[:2]
        p, sx, sy = project(m.verts["pos"], view, size, (lo, hi))
        sz = p[:, 2]
        uv = m.verts["uv"].astype(np.float64)
        nrm = m.verts["nrm"].astype(np.float64) @ R.T
        for tri in m.faces:
            xs, ys, zs = sx[tri], sy[tri], sz[tri]
            x0, x1 = int(max(np.floor(xs.min()), 0)), int(min(np.ceil(xs.max()), size - 1))
            y0, y1 = int(max(np.floor(ys.min()), 0)), int(min(np.ceil(ys.max()), size - 1))
            if x1 < x0 or y1 < y0:
                continue
            det = (xs[1] - xs[0]) * (ys[2] - ys[0]) - (xs[2] - xs[0]) * (ys[1] - ys[0])
            if abs(det) < 1e-9:
                continue
            if cull:
                # 视空间里 cross(b-a, c-a) 的 z 分量 < 0 就是背对观察者
                fn = np.cross(p[tri[1]] - p[tri[0]], p[tri[2]] - p[tri[0]])
                if fn[2] < 0:
                    continue
            gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            w1 = ((gx - xs[0]) * (ys[2] - ys[0]) - (gy - ys[0]) * (xs[2] - xs[0])) / det
            w2 = ((gy - ys[0]) * (xs[1] - xs[0]) - (gx - xs[0]) * (ys[1] - ys[0])) / det
            w0 = 1 - w1 - w2
            inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not inside.any():
                continue
            z = w0 * zs[0] + w1 * zs[1] + w2 * zs[2]
            zb = zbuf[y0:y1 + 1, x0:x1 + 1]
            hit = inside & (z > zb)
            if not hit.any():
                continue
            u = (w0 * uv[tri[0], 0] + w1 * uv[tri[1], 0] + w2 * uv[tri[2], 0]) % 1.0
            v = (w0 * uv[tri[0], 1] + w1 * uv[tri[1], 1] + w2 * uv[tri[2], 1]) % 1.0
            ti = np.clip((u * tw).astype(int), 0, tw - 1)
            tj = np.clip((v * th).astype(int), 0, th - 1)
            n = w0[..., None] * nrm[tri[0]] + w1[..., None] * nrm[tri[1]] + w2[..., None] * nrm[tri[2]]
            n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-9)
            shade = 0.55 + 0.45 * np.clip(n @ L, 0, 1)
            texel = tex[tj, ti, :3].astype(np.float64) * shade[..., None]
            cb = color[y0:y1 + 1, x0:x1 + 1]
            cb[hit] = np.clip(texel[hit], 0, 255).astype(np.uint8)
            zb[hit] = z[hit]
    return (color, zbuf) if return_z else color


def render_uv(m, tex_png, out_png, scale=4):
    from PIL import Image, ImageDraw

    tex = Image.open(tex_png).convert("RGB")
    w, h = tex.width * scale, tex.height * scale
    img = tex.resize((w, h), Image.NEAREST)
    draw = ImageDraw.Draw(img)
    uv = m.verts["uv"]
    for a, b, c in m.faces:
        pts = [(uv[i, 0] * w, uv[i, 1] * h) for i in (a, b, c, a)]
        draw.line(pts, fill=(0, 255, 0), width=1)
    for i in range(m.nv):
        x, y = uv[i, 0] * w, uv[i, 1] * h
        draw.ellipse((x - 1.5, y - 1.5, x + 1.5, y + 1.5), fill=(255, 0, 0))
    img.save(out_png)


# ---------------------------------------------------------------------------

def roundtrip_dir(d):
    ok, bad = 0, []
    for name in sorted(os.listdir(d)):
        if not name.lower().endswith(".msh"):
            continue
        path = os.path.join(d, name)
        blob = open(path, "rb").read()
        try:
            if write(parse(blob, path)) == blob:
                ok += 1
            else:
                bad.append((name, "写回不一致"))
        except Exception as exc:  # noqa: BLE001 —— 任何解析失败都算不过
            bad.append((name, "%s: %s" % (type(exc).__name__, exc)))
    return ok, bad


def cmd_info(paths):
    for p in paths:
        m = load(p)
        pos = m.verts["pos"]
        print("%s: nv=%d ni=%d faces=%d tex=%s subsets=%s effects=%d bones=%d tail=%dB"
              % (os.path.basename(p), m.nv, m.ni, m.ni // 3, m.textures, m.subsets,
                 len(m.effects), len(m.bones), len(m.tail)))
        print("   bbox x[%.2f, %.2f] y[%.2f, %.2f] z[%.2f, %.2f]"
              % (pos[:, 0].min(), pos[:, 0].max(), pos[:, 1].min(), pos[:, 1].max(),
                 pos[:, 2].min(), pos[:, 2].max()))
        print("   bones: %s" % ", ".join("%s(%d)" % (n, _u32(raw, 4 + 2 * len(n)))
                                          for n, raw in m.bones))


def main(argv=None):
    ap = argparse.ArgumentParser(description=".msh 解析 / 写出 / 渲染 / 顶点位移")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("roundtrip"); p.add_argument("dirs", nargs="+")
    p = sub.add_parser("info"); p.add_argument("files", nargs="+")
    p = sub.add_parser("uv"); p.add_argument("msh"); p.add_argument("tex"); p.add_argument("out")
    p = sub.add_parser("render"); p.add_argument("msh", nargs="+"); p.add_argument("--out", required=True)
    p.add_argument("--views", default="front,side,top"); p.add_argument("--size", type=int, default=480)
    p = sub.add_parser("displace"); p.add_argument("msh"); p.add_argument("spec"); p.add_argument("out")
    p.add_argument("--render", help="顺手渲一张改前/改后对照 PNG")
    p = sub.add_parser("preview", help="带贴图渲染若干部件（贴图取同名 .dds）")
    p.add_argument("msh", nargs="+"); p.add_argument("--out", required=True)
    p.add_argument("--views", default="front,rot35,side"); p.add_argument("--size", type=int, default=512)
    args = ap.parse_args(argv)

    if args.cmd == "roundtrip":
        rc = 0
        for d in args.dirs:
            ok, bad = roundtrip_dir(d)
            print("%s: %d ok, %d bad" % (d, ok, len(bad)))
            for name, why in bad[:30]:
                print("  x %s  %s" % (name, why))
            rc |= 1 if bad else 0
        return rc

    if args.cmd == "info":
        cmd_info(args.files)
        return 0

    if args.cmd == "uv":
        render_uv(load(args.msh), args.tex, args.out)
        print("->", args.out)
        return 0

    if args.cmd == "render":
        render([load(p) for p in args.msh], args.out, views=tuple(args.views.split(",")),
               size=args.size, title=", ".join(os.path.basename(p) for p in args.msh))
        print("->", args.out)
        return 0

    if args.cmd == "preview":
        from PIL import Image
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import dds_edit
        parts = []
        for p in args.msh:
            m = load(p)
            tex_path = os.path.join(os.path.dirname(p), m.textures[0])
            parts.append((m, dds_edit.load_rgba(tex_path)[1]))
        views = args.views.split(",")
        sheet = Image.new("RGB", (args.size * len(views), args.size))
        for i, view in enumerate(views):
            sheet.paste(Image.fromarray(rasterize(parts, view=view, size=args.size)), (i * args.size, 0))
        sheet.save(args.out)
        print("->", args.out)
        return 0

    if args.cmd == "displace":
        blob = open(args.msh, "rb").read()
        before = parse(blob, args.msh)
        m = parse(blob, args.msh)
        spec = json.load(open(args.spec, encoding="utf-8"))
        moved, counts = apply_ops(m, spec)
        out = write(m)
        # 自检：除顶点区外逐字节一致
        chk = parse(out, args.out)
        assert chk.nv == before.nv and chk.ni == before.ni
        assert np.array_equal(chk.indices, before.indices)
        assert chk.bones == before.bones and chk.materials == before.materials
        assert chk.adjacency == before.adjacency and chk.head == before.head
        assert chk.tail == before.tail and chk.effects == before.effects
        assert np.array_equal(chk.verts["nrm"], before.verts["nrm"])
        assert np.array_equal(chk.verts["uv"], before.verts["uv"])
        assert len(out) == len(blob)
        with open(args.out, "wb") as fh:
            fh.write(out)
        print("%s -> %s：每步命中 %s，共移动 %d 个顶点；索引/法线/UV/材质/骨骼/邻接逐字节未变 ✓"
              % (args.msh, args.out, counts, moved))
        if args.render:
            hl = np.any(chk.verts["pos"] != before.verts["pos"], axis=1)
            render([before, chk], args.render, highlight=[None, hl],
                   title="before(blue) / after(orange), red = moved")
            print("->", args.render)
        return 0
    return 2


if __name__ == "__main__":
    # ★ 把 stdout / stderr 钉成 utf-8。调用方一**捕获**输出（管道 / 赋值给变量），
    #   CPython 就发现 stdout 不是控制台、改用 `GetACP()` = cp936 —— 中文按 GBK 落进
    #   管道而上游按 utf-8 解（满屏乱码），`✓` 这种 cp936 编不出来的字符更是直接
    #   `UnicodeEncodeError` 把进程带崩。完整来龙去脉见 `tools/pkn.py` 的 main()。
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
