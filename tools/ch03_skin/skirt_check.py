#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skirt_check.py —— 复现 + 验收「裙子上有网格线」（X1 M7 阶段 D）。

    C:\\Python314\\python.exe tools/ch03_skin/skirt_check.py        # 写 tools/ch03_skin/compare_D_skirt.png

## 根因（FINDINGS §17）

不是贴图、不是 UV。`.msh` 末尾那张邻接表是给**模板阴影**抽轮廓边用的：一对邻居面「一面朝光、
一面背光」的边就挤出成阴影体。`geometry.py` 的花瓣是双面薄片（正面 F/F2 + 反绕向副本 F'/F2'），
每条内部边上有 4 个面；`mshtool.build_adjacency` 的旧规则「取第一个面号更大的面当邻居」会写出
`F2→F'`（正面配反面）—— 正反面永远一朝光一背光 ⇒ 花瓣的**每条内部边都成了轮廓边**，
阴影体沿着三角形的边落回裙面上，就是那些一格一格的暗线。

原版 `ch01B0006`（蝴蝶结，15 组反绕向重复面）证明导出工具是**两两配对**：内部边记 `(F,F2)` + `(F',F2')`，
只有边界边才记 `(F,F')`。`build_adjacency` 已改成这条规则（ch01 256 个文件复现 213 → 235 个，无退步）。

## 本脚本做什么

对**当前**的 `ch03B0000.msh`（几何不变）分别用旧规则 / 新规则算邻接表，再用 `mshtool.silhouette_edges`
按同一个光向抽出「引擎会挤出阴影体的边」，叠画在贴图渲染上：

- 左：128×128 贴图放大 + UV 线框（排除贴图 / UV 的证据：每片花瓣独占一块连续渐变区，缝只在花瓣外沿）；
- 中：旧邻接表 ⇒ 花瓣内部每条边都是轮廓边（实机看到的网格线）；
- 右：新邻接表 ⇒ 只剩花瓣外沿。

渲染是软光栅，只为看「哪些边会被抽成轮廓」，不等于游戏画面。
"""
from __future__ import annotations

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
import mshtool  # noqa: E402

CH03 = os.path.join(ROOT, "game_patched", "Pack_develop", "Models", "Characters", "ch03")
LIGHT = (0.3, 0.5, 0.8)


def old_rule_adjacency(positions, faces):
    """2026-09-15 之前 mshtool.build_adjacency 的规则：每条边取第一个面号更大的面当邻居。只为复现，别再用。"""
    faces = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
    key_of = {}
    g = np.empty(len(positions), dtype=np.int64)
    for i, p in enumerate(map(tuple, np.asarray(positions, dtype="<f4"))):
        g[i] = key_of.setdefault(p, len(key_of))
    edge_map = {}
    for fi, (a, b, c) in enumerate(faces):
        for u, v in ((a, b), (b, c), (c, a)):
            edge_map.setdefault(frozenset((int(g[u]), int(g[v]))), []).append(fi)
    out = [struct.pack("<I", len(faces))]
    for fi, (a, b, c) in enumerate(faces):
        nbs = []
        for u, v in ((a, b), (b, c), (c, a)):
            others = [f for f in edge_map[frozenset((int(g[u]), int(g[v])))] if f > fi]
            if others:
                nbs.append((others[0], int(u), int(v)))
        nb = [n for n, _, _ in nbs] + [0xFFFF] * (3 - len(nbs))
        ed = [x for _, u, v in nbs for x in (u, v)] + [0] * (6 - 2 * len(nbs))
        out.append(struct.pack("<II", fi, len(nbs)) + struct.pack("<3H", *nb) + struct.pack("<6H", *ed) + bytes(14))
    return b"".join(out)


def with_adjacency(m, adj):
    mm = mshtool.Mesh()
    for k in mshtool.Mesh.__slots__:
        setattr(mm, k, getattr(m, k))
    mm.adjacency = adj
    return mm


def labeled(img, text, width, height):
    canvas = Image.new("RGB", (width, height), (30, 30, 34))
    scale = min((width - 8) / img.width, (height - 28) / img.height)
    im = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                    Image.NEAREST if scale > 1.5 else Image.LANCZOS)
    canvas.paste(im, ((width - im.width) // 2, 24 + (height - 24 - im.height) // 2))
    ImageDraw.Draw(canvas).text((6, 6), text, fill=(230, 230, 230))
    return canvas


def main():
    parts = []
    for n in ("ch03B0000", "ch03L0000", "ch03S0000", "ch03G0000"):
        m = mshtool.load(os.path.join(CH03, n + ".msh"))
        parts.append((m, dds_edit.load_rgba(os.path.join(CH03, m.textures[0]))[1]))
    body, tex = parts[0]
    size = 520
    allpos = np.concatenate([m.verts["pos"] for m, _ in parts])
    bbox = (allpos.min(0), allpos.max(0))

    # 左：贴图放大 + UV 线框
    uvimg = Image.fromarray(tex[..., :3]).resize((512, 512), Image.NEAREST)
    d = ImageDraw.Draw(uvimg)
    uv = body.verts["uv"]
    for a, b, c in body.faces:
        d.line([(uv[i, 0] * 512, uv[i, 1] * 512) for i in (a, b, c, a)], fill=(0, 255, 0), width=1)

    variants = [("OLD adjacency (first-higher-face rule): predicted in-game shadow edges", old_rule_adjacency(body.verts["pos"], body.faces)),
                ("NEW adjacency (pairwise rule, as ch01B0006): predicted shadow edges", body.adjacency)]
    n_edges = []
    panels = [labeled(uvimg, "ch03B0000.dds x4 + UV wireframe (each petal = one continuous gradient block)", size, size + 24)]
    for label, adj in variants:
        mm = with_adjacency(body, adj)
        edges = mshtool.silhouette_edges(mm, LIGHT)
        n_edges.append(len(edges))
        row = Image.new("RGB", (size * 2, size))
        for i, view in enumerate(("front", "rot35")):
            color, z = mshtool.rasterize(parts, view=view, size=size, bbox=bbox, light=LIGHT, return_z=True)
            color = mshtool.draw_edges(color, z, mm, edges, view, size, bbox)
            row.paste(Image.fromarray(color), (i * size, 0))
        panels.append(labeled(row, "%s  [%d edges]" % (label, len(edges)), size * 2, size + 24))
    sheet = Image.new("RGB", (sum(p.width for p in panels), size + 24), (30, 30, 34))
    x = 0
    for p in panels:
        sheet.paste(p, (x, 0))
        x += p.width
    out = os.path.join(HERE, "compare_D_skirt.png")
    sheet.save(out)
    print("轮廓边数：旧规则 %d，新规则 %d  -> %s" % (n_edges[0], n_edges[1], out))
    # 数一数：新表里还有没有「正面配反面」的内部边
    pos = body.verts["pos"].astype(np.float64)
    f = body.faces
    fn = np.cross(pos[f[:, 1]] - pos[f[:, 0]], pos[f[:, 2]] - pos[f[:, 0]])
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    bad = sum(1 for fi, nb, u, v in mshtool.adjacency_pairs(body) if fn[fi] @ fn[nb] < -0.99)
    print("新表里法线相反（正面配反面）的邻居对：%d" % bad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
