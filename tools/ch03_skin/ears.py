#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ears.py —— 给爱琳做精灵尖耳：从母本 ch01H0000.msh 出发，改贴图名 + 按 ears.json 移顶点，写出 ch03H0000.msh。

    C:\\Python314\\python.exe tools/ch03_skin/ears.py            # 写 ch03H0000.msh + 对照图
    C:\\Python314\\python.exe tools/ch03_skin/ears.py --dry-run  # 只渲对照图，不写 .msh

## 为什么从 ch01 出发而不是在 ch03 上原地改

原地改不幂等：跑两次耳朵就长两倍。从只读母本出发，每次都是「克隆（同 mkchar：只换贴图名）→ 位移」，
重跑一万次结果都一样。⇒ 也意味着 **`mkchar.py --from ch01 --to ch03` 重新克隆之后，必须再跑一遍
本脚本和 recolor.py**，否则耳朵和贴图都会被母本盖回去。

## 只动位置，别的一个字节不碰（铁律）

`mshtool.apply_ops` 只写 `verts["pos"]`。写出前断言：索引 / 法线 / UV / 材质块 / 骨骼块 / 邻接表
和克隆件逐字节相同，文件长度相同。骨骼按名字在角色骨架里找，找不到返 NULL 直接崩（V0.3商店 §50），
所以骨骼块必须原样。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
ROOT = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
import mkchar  # noqa: E402
import mshtool  # noqa: E402

CHARS = os.path.join(ROOT, "game_patched", "Pack_develop", "Models", "Characters")
SRC = os.path.join(CHARS, "ch01", "ch01H0000.msh")
DST = os.path.join(CHARS, "ch03", "ch03H0000.msh")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default=os.path.join(HERE, "ears.json"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--render", default=os.path.join(HERE, "compare_B_ears.png"))
    args = ap.parse_args(argv)

    blob = open(SRC, "rb").read()
    blob, hits = mkchar.patch_msh(blob, {"ch01H0000.dds": "ch03H0000.dds"})
    if hits != 1:
        raise SystemExit("母本里贴图名命中 %d 处，预期 1" % hits)
    before = mshtool.parse(blob, SRC)
    if before.textures != ["ch03H0000.dds"]:
        raise SystemExit("贴图名没改对：%r" % before.textures)

    m = mshtool.parse(blob, SRC)
    spec = json.load(open(args.spec, encoding="utf-8"))
    moved, counts = mshtool.apply_ops(m, spec)
    out = mshtool.write(m)

    chk = mshtool.parse(out, DST)
    assert len(out) == len(blob), "长度变了"
    assert chk.nv == before.nv and chk.ni == before.ni
    assert np.array_equal(chk.indices, before.indices)
    assert np.array_equal(chk.verts["nrm"], before.verts["nrm"])
    assert np.array_equal(chk.verts["uv"], before.verts["uv"])
    assert chk.materials == before.materials and chk.bones == before.bones
    assert chk.adjacency == before.adjacency and chk.tail == before.tail
    assert chk.head == before.head and chk.effects == before.effects and chk.pad == before.pad
    changed = np.any(chk.verts["pos"] != before.verts["pos"], axis=1)
    print("每步命中 %s，共移动 %d 个顶点：%s" % (counts, moved, np.where(changed)[0].tolist()))
    for i in np.where(changed)[0]:
        print("  v%-3d %s -> %s" % (i, np.round(before.verts["pos"][i], 2), np.round(chk.verts["pos"][i], 2)))
    print("索引 / 法线 / UV / 材质 / 骨骼 / 邻接 / 文件长度 逐字节未变 ✓")

    hair = mshtool.load(os.path.join(CHARS, "ch03", "ch0300000.msh"))
    mshtool.render([before, chk, hair], args.render, views=("front", "side", "top"), size=440,
                   highlight=[None, changed, None],
                   title="ch03H0000.msh  blue=before  orange=after (red = moved verts)  green=hair")
    print("->", args.render)

    if args.dry_run:
        return 0
    try:
        same = open(DST, "rb").read() == out
    except OSError:
        same = False
    if same:
        print("%s 已经是这个内容，未写" % DST)
    else:
        with open(DST, "wb") as fh:
            fh.write(out)
        print("-> %s（%d B）" % (DST, len(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
