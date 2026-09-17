#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""report.py —— 渲出 M7 的验收对照图（X1 爱琳外观改造）。

    C:\\Python314\\python.exe tools/ch03_skin/report.py

产出（都在 tools/ch03_skin/）：
- `compare_A.png`  四联对照：卡希尔母本渲染 | 爱琳现状渲染 | 官方头像帧 27 | 用户原画
- `textures_before_after.png`  6 张默认贴图改前 / 改后
- `faces_after.png`  13 张表情贴图改后（检查 12 帧表情眼睛是不是都换了）

渲染用的是 mshtool.rasterize 的软光栅（无蒙皮、简单兰伯特），只为看配色和形状，不等于游戏画面。
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

PACK = os.path.join(ROOT, "game_patched", "Pack_develop")
CHARS = os.path.join(PACK, "Models", "Characters")
CONCEPT = os.path.join(ROOT, "develop_history", "X_自定义游戏内容Mod开发", ".claude", "refs", "irene-concept.png")
PARTS = ["0", "H", "B", "L", "G", "S"]
EXPR = ["", "Angry", "Blink", "Cry", "Damage", "Fire", "Hi", "ILoveYou", "Laugh", "Lose", "Oops", "Win", "Wink"]


def load_parts(ch):
    d = os.path.join(CHARS, ch)
    out = []
    for p in PARTS:
        m = mshtool.load(os.path.join(d, "%s%s0000.msh" % (ch, p)))
        out.append((m, dds_edit.load_rgba(os.path.join(d, m.textures[0]))[1]))
    return out


def render_body(ch, views, size, bbox=None):
    parts = load_parts(ch)
    if bbox is None:
        allpos = np.concatenate([m.verts["pos"] for m, _ in parts])
        bbox = (allpos.min(0), allpos.max(0))
    sheet = Image.new("RGB", (size * len(views), size))
    for i, v in enumerate(views):
        sheet.paste(Image.fromarray(mshtool.rasterize(parts, view=v, size=size, bbox=bbox)), (i * size, 0))
    return sheet, bbox


def portrait_frame(idx):
    """BigChrIcons.smf 的第 idx 帧（.smf：u32 版本 + u32 帧数 + 帧 × 8×i32）。"""
    smf = open(os.path.join(PACK, "Images", "NewUI2", "BigChrIcons.smf"), "rb").read()
    x1, y1, x2, y2 = struct.unpack_from("<4i", smf, 8 + idx * 32)
    png = Image.open(os.path.join(PACK, "Images", "NewUI2", "BigChrIcons.png")).convert("RGB")
    return png.crop((x1, y1, x2, y2))


def labeled(img, text, width, height):
    canvas = Image.new("RGB", (width, height), (30, 30, 34))
    scale = min((width - 8) / img.width, (height - 28) / img.height)
    im = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                    Image.NEAREST if scale > 1.5 else Image.LANCZOS)
    canvas.paste(im, ((width - im.width) // 2, 24 + (height - 24 - im.height) // 2))
    ImageDraw.Draw(canvas).text((6, 6), text, fill=(230, 230, 230))
    return canvas


def main():
    views = ("front", "rot35")
    size = 400
    before, bbox = render_body("ch01", views, size)
    after, _ = render_body("ch03", views, size, bbox)
    head = Image.fromarray(mshtool.rasterize(load_parts("ch03")[:2], view="front", size=size))
    portrait = portrait_frame(27)
    concept = Image.open(CONCEPT).convert("RGB") if os.path.exists(CONCEPT) else None

    W, H = 2 * size, size + 24
    panels = [
        labeled(before, "1. ch01 Casil (source clone, before)", W, H),
        labeled(after, "2. ch03 Irene (after recolor + ears)", W, H),
        labeled(head, "2b. ch03 head close-up", size, H),
        labeled(portrait, "3. official portrait BigChrIcons frame 27", size, H),
    ]
    if concept is not None:
        panels.append(labeled(concept, "4. user concept art refs/irene-concept.png", size, H))
    total_w = sum(p.width for p in panels)
    sheet = Image.new("RGB", (total_w, H), (30, 30, 34))
    x = 0
    for p in panels:
        sheet.paste(p, (x, 0))
        x += p.width
    out = os.path.join(HERE, "compare_A.png")
    sheet.save(out)
    print("->", out)

    # 贴图改前 / 改后
    S = 160
    names = ["%s%s0000" % ("ch03", p) for p in PARTS]
    sheet = Image.new("RGB", (S * len(names), S * 2 + 20), (30, 30, 34))
    d = ImageDraw.Draw(sheet)
    for i, n in enumerate(names):
        src = n[:2] + "01" + n[4:]
        a = dds_edit.load_rgba(os.path.join(CHARS, "ch01", src + ".dds"))[1]
        b = dds_edit.load_rgba(os.path.join(CHARS, "ch03", n + ".dds"))[1]
        sheet.paste(Image.fromarray(a[..., :3]).resize((S, S), Image.NEAREST), (i * S, 20))
        sheet.paste(Image.fromarray(b[..., :3]).resize((S, S), Image.NEAREST), (i * S, 20 + S))
        d.text((i * S + 4, 4), n + ".dds", fill=(230, 230, 230))
    out = os.path.join(HERE, "textures_before_after.png")
    sheet.save(out)
    print("->", out)

    sheet = Image.new("RGB", (128 * 7 + 16, 128 * 2 + 6), (30, 30, 34))
    for i, e in enumerate(EXPR):
        b = dds_edit.load_rgba(os.path.join(CHARS, "ch03", "ch03H0000%s.dds" % e))[1]
        sheet.paste(Image.fromarray(b[..., :3]), (2 + (i % 7) * 130, 2 + (i // 7) * 130))
    out = os.path.join(HERE, "faces_after.png")
    sheet.save(out)
    print("->", out)
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
