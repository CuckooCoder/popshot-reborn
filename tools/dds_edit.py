#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dds_edit.py —— 只换像素区、**128 字节头一个字节不动**的 DDS 读写器（X_Mod · X1 爱琳 M7）。

    python tools/dds_edit.py info    a.dds [b.dds ...]         # 打印头里的格式
    python tools/dds_edit.py export  a.dds out.png             # 像素区 -> PNG（RGBA）
    python tools/dds_edit.py import  template.dds in.png out.dds   # 头抄 template，像素来自 PNG
    python tools/dds_edit.py roundtrip DIR [DIR ...]           # 目录里所有 .dds 解->编 逐字节自检

## 为什么不用 `PIL.Image.save('*.dds')`

Pillow 写 DDS 时自己决定头（会写 pitch、mip 数、可能换成 DXT / 32 位），
**不保证吐出客户端原来那种 R5G6B5 / 无 mipmap 的头**。而这个 2007 年的引擎读 DDS
是按头里的 DDPIXELFORMAT 硬解的，头一变就是花屏或崩。
⇒ 本脚本的 `import` 把模板文件的 `[0:128]` 原样抄过来，只重编 `[128:]`，
结束时**断言输出长度和模板完全相同**，不等就报错退出，什么都不写。

## 格式（FINDINGS §7，✅实测）

ch01/ch03 的 268 张里：117 张 64×64 R5G6B5（8320 B）、122 张 64×64 A8R8G8B8（16512 B）、
17 张 128×128 R5G6B5（32896 B），另有 2 张 128×128 A8R8G8B8、2 张 16×16 A8R8G8B8，
以及 **8 张 DXT1/DXT3（带 7 级 mipmap）—— 只能 export 看，不许 import**。

未压缩格式按 `DDPIXELFORMAT` 的位掩码通用解/编：
- `pfflags & 0x40` (RGB)：`RGBBitCount` 位/像素，小端，各通道按掩码取位；
- `pfflags & 0x01` (ALPHAPIXELS)：额外有 A 掩码；没有就当不透明（导出 A=255，导入丢 A）；
- 位深 n → 8 位：`round(v * 255 / (2^n-1))`；反向同理。**这一对变换是无损的**
  （8 位一格 > 1，来回舍入落不到隔壁），`roundtrip` 子命令就是拿这条在全目录上验。
- 有 mipmap（`mips > 1`）的未压缩文件：像素区是逐级 BOX 缩小的链。本仓库里没有这种，
  但 `import` 还是照链重建，长度断言兜底。
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from collections import namedtuple

import numpy as np
from PIL import Image

HEADER_LEN = 128

Header = namedtuple("Header", "w h mips fourcc bits masks flags pfflags")


# ---------------------------------------------------------------------------
# 头
# ---------------------------------------------------------------------------

def parse_header(blob):
    if len(blob) < HEADER_LEN or blob[:4] != b"DDS ":
        raise ValueError("不是 DDS 文件（magic 不对或不足 128 字节）")
    size, flags, h, w, _pitch, _depth, mips = struct.unpack_from("<7I", blob, 4)
    if size != 124:
        raise ValueError("DDS_HEADER.dwSize=%d != 124" % size)
    pfsize, pfflags, fourcc, bits, rm, gm, bm, am = struct.unpack_from("<II4sIIIII", blob, 76)
    if pfsize != 32:
        raise ValueError("DDPIXELFORMAT.dwSize=%d != 32" % pfsize)
    if pfflags & 0x04:  # DDPF_FOURCC
        fmt = fourcc.decode("ascii", "replace")
        masks = None
    elif pfflags & 0x40:  # DDPF_RGB
        fmt = None
        masks = (rm, gm, bm, am if (pfflags & 0x01) else 0)
    else:
        raise ValueError("不认识的 DDPIXELFORMAT.dwFlags=%#x" % pfflags)
    return Header(w, h, max(mips, 1), fmt, bits, masks, flags, pfflags)


def describe(hd):
    if hd.fourcc:
        return "%dx%d %s mips=%d" % (hd.w, hd.h, hd.fourcc, hd.mips)
    names = []
    for ch, m in zip("RGBA", hd.masks):
        if m:
            names.append("%s%d" % (ch, bin(m).count("1")))
    # 按掩码从高位到低位排，得到 A8R8G8B8 / R5G6B5 这种习惯写法
    names.sort(key=lambda s: -[m for ch, m in zip("RGBA", hd.masks) if ch == s[0]][0])
    return "%dx%d %s(%d bit) mips=%d" % (hd.w, hd.h, "".join(names), hd.bits, hd.mips)


def _mask_shift_bits(mask):
    if not mask:
        return 0, 0
    shift = (mask & -mask).bit_length() - 1
    nbits = bin(mask).count("1")
    return shift, nbits


# ---------------------------------------------------------------------------
# 未压缩格式的解 / 编（numpy，向量化）
# ---------------------------------------------------------------------------

def _decode_level(raw, w, h, hd):
    bpp = hd.bits // 8
    if len(raw) != w * h * bpp:
        raise ValueError("像素区长度 %d != %d×%d×%d" % (len(raw), w, h, bpp))
    if bpp == 2:
        px = np.frombuffer(raw, dtype="<u2").astype(np.uint32)
    elif bpp == 4:
        px = np.frombuffer(raw, dtype="<u4").astype(np.uint32)
    elif bpp == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.uint32)
        px = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
    else:
        raise ValueError("不支持 %d 位/像素" % hd.bits)
    out = np.empty((w * h, 4), dtype=np.uint8)
    for i, mask in enumerate(hd.masks):
        if not mask:
            out[:, i] = 255 if i == 3 else 0
            continue
        shift, nbits = _mask_shift_bits(mask)
        v = (px & mask) >> shift
        full = (1 << nbits) - 1
        out[:, i] = np.rint(v.astype(np.float64) * 255.0 / full).astype(np.uint8)
    return out.reshape(h, w, 4)


def _encode_level(rgba, hd):
    h, w, _ = rgba.shape
    flat = rgba.reshape(-1, 4).astype(np.float64)
    px = np.zeros(w * h, dtype=np.uint32)
    for i, mask in enumerate(hd.masks):
        if not mask:
            continue
        shift, nbits = _mask_shift_bits(mask)
        full = (1 << nbits) - 1
        v = np.rint(flat[:, i] * full / 255.0).astype(np.uint32)
        px |= (v << shift) & mask
    bpp = hd.bits // 8
    if bpp == 2:
        return px.astype("<u2").tobytes()
    if bpp == 4:
        return px.astype("<u4").tobytes()
    if bpp == 3:
        b = np.stack([px & 0xFF, (px >> 8) & 0xFF, (px >> 16) & 0xFF], axis=1)
        return b.astype(np.uint8).tobytes()
    raise ValueError("不支持 %d 位/像素" % hd.bits)


def _mip_dims(w, h, level):
    return max(w >> level, 1), max(h >> level, 1)


def decode(blob):
    """DDS 字节 -> (Header, RGBA uint8 ndarray of level 0)。DXT 走 Pillow（只为看图）。"""
    hd = parse_header(blob)
    if hd.fourcc:
        import io
        img = Image.open(io.BytesIO(blob)).convert("RGBA")
        return hd, np.asarray(img)
    bpp = hd.bits // 8
    n0 = hd.w * hd.h * bpp
    return hd, _decode_level(blob[HEADER_LEN:HEADER_LEN + n0], hd.w, hd.h, hd)


def encode(template_blob, rgba):
    """用 template 的 128 字节头 + 新像素拼出完整 DDS；长度必须和 template 一致。"""
    hd = parse_header(template_blob)
    if hd.fourcc:
        raise ValueError("模板是 %s 压缩纹理，本脚本不写压缩格式" % hd.fourcc)
    if rgba.shape[:2] != (hd.h, hd.w):
        raise ValueError("PNG 是 %dx%d，模板是 %dx%d" % (rgba.shape[1], rgba.shape[0], hd.w, hd.h))
    parts = [template_blob[:HEADER_LEN]]
    img = Image.fromarray(rgba, "RGBA")
    for level in range(hd.mips):
        w, h = _mip_dims(hd.w, hd.h, level)
        lv = img if level == 0 else img.resize((w, h), Image.BOX)
        parts.append(_encode_level(np.asarray(lv), hd))
    out = b"".join(parts)
    if len(out) != len(template_blob):
        raise ValueError("输出 %d 字节 != 模板 %d 字节，拒绝写出" % (len(out), len(template_blob)))
    return out


# ---------------------------------------------------------------------------
# 文件级封装
# ---------------------------------------------------------------------------

def load_rgba(path):
    blob = open(path, "rb").read()
    hd, rgba = decode(blob)
    return hd, rgba


def save_like(template_path, rgba, out_path):
    """把 rgba 按 template 的头写成 out_path；返回写出的字节数。"""
    template = open(template_path, "rb").read()
    out = encode(template, rgba)
    with open(out_path, "wb") as fh:
        fh.write(out)
    return len(out)


def roundtrip_dir(d):
    ok = skipped = 0
    bad = []
    for name in sorted(os.listdir(d)):
        if not name.lower().endswith(".dds"):
            continue
        path = os.path.join(d, name)
        blob = open(path, "rb").read()
        hd = parse_header(blob)
        if hd.fourcc:
            skipped += 1
            continue
        _, rgba = decode(blob)
        if encode(blob, rgba) == blob:
            ok += 1
        else:
            bad.append(name)
    return ok, skipped, bad


# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="DDS 像素区读写（头 128 字节原样保留）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info");      p.add_argument("files", nargs="+")
    p = sub.add_parser("export");    p.add_argument("dds"); p.add_argument("png")
    p = sub.add_parser("import");    p.add_argument("template"); p.add_argument("png"); p.add_argument("out")
    p = sub.add_parser("roundtrip"); p.add_argument("dirs", nargs="+")
    args = ap.parse_args(argv)

    if args.cmd == "info":
        for f in args.files:
            blob = open(f, "rb").read()
            print("%-40s %7d B  %s" % (os.path.basename(f), len(blob), describe(parse_header(blob))))
        return 0

    if args.cmd == "export":
        hd, rgba = load_rgba(args.dds)
        Image.fromarray(rgba, "RGBA").save(args.png)
        print("%s (%s) -> %s" % (args.dds, describe(hd), args.png))
        return 0

    if args.cmd == "import":
        rgba = np.asarray(Image.open(args.png).convert("RGBA"))
        n = save_like(args.template, rgba, args.out)
        print("%s + %s -> %s (%d B，和模板等长 ✓)" % (args.template, args.png, args.out, n))
        return 0

    if args.cmd == "roundtrip":
        rc = 0
        for d in args.dirs:
            ok, skipped, bad = roundtrip_dir(d)
            print("%s：%d 张解->编逐字节一致，%d 张压缩格式跳过，%d 张不一致"
                  % (d, ok, skipped, len(bad)))
            if bad:
                rc = 1
                for name in bad[:20]:
                    print("  ✗ " + name)
        return rc
    return 2


if __name__ == "__main__":
    sys.exit(main())
