#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mtntool.py —— 角色动作 `.mtn` 的解析 / 骨架树 / 姿态求值 / 运动幅度统计（X_Mod · X1 爱琳 M7 阶段 C）。

    python tools/mtntool.py tree  a.mtn              # 打印 229 根骨头的父子树 + 每根有没有动画轨
    python tools/mtntool.py stats DIR                # 每根骨头在目录下全部动作里的运动幅度（决定新几何挂哪根骨）
    python tools/mtntool.py check DIR                # 目录下所有 .mtn 的骨架树是否完全一致（名字 + 父子关系）

## 格式（✅ 在 ch01 全部 86 个 .mtn 上解析到文件末尾恰好用完，0 字节剩余）

```
u32 版本 = 3
wstr "AnimationSet0"                      （u32 码元数 + UTF-16LE）
f64 ticks/秒 = 3600      f64 时长（秒）
u32 骨骼数 = 229         u32 0
骨架树：每个节点 = wstr 名字 + 16 × f32 局部矩阵 + 控制码序列
    ★ 序列化方式和 D3DXFRAME 完全一样（pFrameFirstChild / pFrameSibling 两个指针）：
        write(node): 名字, 矩阵;
                     若有子节点:   写 1, 然后递归写第一个子节点
                     若有兄弟节点: 写 2, 然后递归写下一个兄弟
                     写 3
    ⇒ 1 的个数 = 有子节点的节点数(173)，2 的个数 = 有兄弟的节点数(55)，3 的个数 = 节点数(229)。
      「兄弟」是嵌在前一个兄弟的递归里写的，所以一串兄弟的收尾 3 会挤在最后一个叶子后面
      —— Bone_Hair_R03 后面连着 13 个 3 就是这么来的，不是「弹栈 13 层」。
u32 轨道数（ch01 = 126，剩下 103 根没有轨道的骨头用树里那个静态矩阵）
每条轨道：wstr 骨骼名
          u32 n + n × (f32 时刻, f32 qx qy qz qw)      旋转关键帧（四元数，D3DX 顺序 x y z w）
          u32 n + n × (f32 时刻, f32 x y z)            平移关键帧
          u32 n + n × (f32 时刻, f32 sx sy sz)         缩放关键帧
```

时刻以 tick 计，帧间隔 120 tick = 1/30 秒。局部矩阵 = S · R · T（D3DX 行向量约定），
世界矩阵 = 局部 · 父世界。

## 单位

动画在 3ds Max 场景单位里；默认部件 `.msh`（`0x04` 处世界矩阵 = 均匀缩放 2.8791 的那一组）
的顶点在 **场景单位 / 2.8791** 里。`.msh` 骨骼块里那 16 个 float 是 **绑定姿态世界矩阵的逆**
（offset matrix），它已经把这个缩放吃进去了：`inv(offset)` 的平移行就是骨头在网格空间里的绑定位置，
旋转行的模长是 0.347。绑定姿态**不等于**任何一帧动画（Stand00 第 0 帧的头在 y=69.2 场景单位，
绑定在 70.96）。

⇒ 给网格里**没有**的骨头补 offset 时，从它父骨头的 `inv(offset)` 出发：
`bind(子) = 局部(子, 场景单位) · bind(父)` —— 父矩阵的旋转行已经带着 0.347 的缩放，
**平移不要再除一次 2.8791**（2026-09-15 踩过：除两次之后兔耳骨跑到头里面去了）。
"""
from __future__ import annotations

import argparse
import glob
import math
import os
import struct
import sys

import numpy as np

TICKS_PER_FRAME = 120.0


class Mtn:
    __slots__ = ("path", "set_name", "ticks_per_sec", "duration", "nodes", "tracks", "track_order", "tail")

    def __init__(self):
        self.nodes = []        # [(名字, 父下标 或 -1, 4x4 静态局部矩阵)]
        self.tracks = {}       # 名字 -> (rot[n,5], pos[n,4], scl[n,4])
        self.track_order = []
        self.tail = b""        # 轨道之后的残片（只有 Crouch-Start 有，见 parse）

    @property
    def names(self):
        return [n for n, _, _ in self.nodes]

    def parent_name(self, name):
        i = self.names.index(name)
        p = self.nodes[i][1]
        return self.nodes[p][0] if p >= 0 else None


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _wstr(b, o):
    n = _u32(b, o)
    if n > 256:
        raise ValueError("字符串长度 %d 不合理 @%#x" % (n, o))
    return b[o + 4: o + 4 + 2 * n].decode("utf-16le"), o + 4 + 2 * n


def _pack_wstr(s):
    return struct.pack("<I", len(s)) + s.encode("utf-16le")


def write(m):
    """parse() 的逆：Mtn -> bytes（✅ 见 `roundtrip` 命令：ch00~ch110 全部 .mtn 逐字节一致）。

    骨架树按 D3DXFRAME 的序列化规则重新写（1 = 第一个子节点，2 = 下一个兄弟，3 = 收尾）；
    子节点顺序 = 它们在 `m.nodes` 里出现的顺序（parse 是先序读入的，所以顺序天然保住了）。
    轨道按 `track_order` 写。矩阵 / 关键帧是 f32 原样升成 f64 存的，写回 f32 不丢一位。
    """
    out = bytearray()
    out += struct.pack("<I", 3)
    out += _pack_wstr(m.set_name)
    out += struct.pack("<dd", m.ticks_per_sec, m.duration)
    out += struct.pack("<II", len(m.nodes), 0)
    children = {}
    for i, (_, parent, _) in enumerate(m.nodes):
        children.setdefault(parent, []).append(i)
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 4000))

    def emit(i):
        name, parent, mat = m.nodes[i]
        out.extend(_pack_wstr(name))
        out.extend(np.asarray(mat, dtype="<f4").reshape(16).tobytes())
        ch = children.get(i)
        if ch:
            out.extend(struct.pack("<I", 1))
            emit(ch[0])
        sibs = children[parent]
        k = sibs.index(i)
        if k + 1 < len(sibs):
            out.extend(struct.pack("<I", 2))
            emit(sibs[k + 1])
        out.extend(struct.pack("<I", 3))

    emit(children[-1][0])
    out += struct.pack("<I", len(m.track_order))
    for name in m.track_order:
        rot, posk, scl = m.tracks[name]
        out += _pack_wstr(name)
        for keys, width in ((rot, 5), (posk, 4), (scl, 4)):
            keys = np.asarray(keys, dtype="<f4").reshape(-1, width)
            out += struct.pack("<I", len(keys)) + keys.tobytes()
    out += m.tail
    return bytes(out)


def save(m, path):
    blob = write(m)
    chk = parse_bytes(blob, path)
    if write(chk) != blob:
        raise SystemExit("%s：写出后再解析不一致，拒绝落盘" % path)
    with open(path, "wb") as f:
        f.write(blob)
    return blob


def parse_bytes(b, path=""):
    m = _parse(b)
    m.path = path
    return m


def parse(path):
    m = _parse(open(path, "rb").read())
    m.path = path
    return m


def _parse(b):
    m = Mtn()
    if _u32(b, 0) != 3:
        raise ValueError("版本不是 3")
    m.set_name, off = _wstr(b, 4)
    m.ticks_per_sec, m.duration = struct.unpack_from("<dd", b, off)
    off += 16
    n_nodes, zero = struct.unpack_from("<II", b, off)
    off += 8
    if zero != 0:
        raise ValueError("骨骼数后面的 u32 不是 0：%d" % zero)

    pos = [off]
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 4000))

    def frame(parent):
        name, o = _wstr(b, pos[0])
        mat = np.array(struct.unpack_from("<16f", b, o), dtype=np.float64).reshape(4, 4)
        o += 64
        me = len(m.nodes)
        m.nodes.append((name, parent, mat))
        pos[0] = o
        c = _u32(b, pos[0]); pos[0] += 4
        if c == 1:                      # 第一个子节点
            frame(me)
            c = _u32(b, pos[0]); pos[0] += 4
        if c == 2:                      # 下一个兄弟
            frame(parent)
            c = _u32(b, pos[0]); pos[0] += 4
        if c != 3:
            raise ValueError("节点 %s 之后期望 3，实际 %d @%#x" % (name, c, pos[0] - 4))

    frame(-1)
    off = pos[0]
    if len(m.nodes) != n_nodes:
        raise ValueError("树里 %d 个节点，头里写 %d" % (len(m.nodes), n_nodes))

    n_tr = _u32(b, off); off += 4
    for _ in range(n_tr):
        name, off = _wstr(b, off)
        nr = _u32(b, off); off += 4
        rot = np.frombuffer(b, "<f4", nr * 5, off).reshape(nr, 5).astype(np.float64); off += nr * 20
        npk = _u32(b, off); off += 4
        posk = np.frombuffer(b, "<f4", npk * 4, off).reshape(npk, 4).astype(np.float64); off += npk * 16
        ns = _u32(b, off); off += 4
        scl = np.frombuffer(b, "<f4", ns * 4, off).reshape(ns, 4).astype(np.float64); off += ns * 16
        m.tracks[name] = (rot, posk, scl)
        m.track_order.append(name)
    # ch01@Crouch-Start.mtn 的 78 条轨道读完后还挂着 35602 B —— 内容是另一段更长的关键帧列表的中段
    # （时刻从 4560 tick 起、没有任何头），和 ch0300011.msh 邻接表后面那 676 B 一样是导出工具复用缓冲区
    # 留下的垃圾。引擎按轨道数读完就停，这里按不透明字节保留。其余 85 个文件恰好读到末尾。
    m.tail = b[off:]
    return m


# ---------------------------------------------------------------------------
# 姿态求值
# ---------------------------------------------------------------------------

def quat_to_mat(q):
    """D3DXMatrixRotationQuaternion（行向量约定）。"""
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w), 0],
        [2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w), 0],
        [2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y), 0],
        [0, 0, 0, 1.0]])


def _key_at(keys, t):
    """取时刻 t 的关键帧值（线性插值；四元数用归一化线性插值，够看的）。"""
    times = keys[:, 0]
    if t <= times[0] or len(keys) == 1:
        return keys[0, 1:]
    if t >= times[-1]:
        return keys[-1, 1:]
    i = int(np.searchsorted(times, t, side="right") - 1)
    t0, t1 = times[i], times[i + 1]
    a = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
    return keys[i, 1:] * (1 - a) + keys[i + 1, 1:] * a


def local_at(m, name, t):
    """骨头 name 在时刻 t（tick）的局部矩阵。没有轨道的骨头返回树里的静态矩阵。"""
    tr = m.tracks.get(name)
    if tr is None:
        return m.nodes[m.names.index(name)][2]
    rot, posk, scl = tr
    q = _key_at(rot, t)
    q = q / max(np.linalg.norm(q), 1e-9)
    p = _key_at(posk, t)
    s = _key_at(scl, t)
    S = np.diag([s[0], s[1], s[2], 1.0])
    T = np.eye(4)
    T[3, :3] = p
    return S @ quat_to_mat(q) @ T


def world_mats(m, t):
    """时刻 t 所有骨头的世界矩阵（场景单位），dict 名字 -> 4x4。"""
    out = {}
    names = m.names
    for name, parent, _ in m.nodes:
        L = local_at(m, name, t)
        out[name] = L @ out[names[parent]] if parent >= 0 else L
    return out


def frame_times(m):
    n = int(round(m.duration * m.ticks_per_sec / TICKS_PER_FRAME))
    return [k * TICKS_PER_FRAME for k in range(n + 1)]


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------

def cmd_tree(path):
    m = parse(path)
    print("%s: %s  %g tick/s  %.3f s (%d 帧)  %d 骨  %d 轨"
          % (os.path.basename(path), m.set_name, m.ticks_per_sec, m.duration,
             len(frame_times(m)) - 1, len(m.nodes), len(m.tracks)))
    depth = {}
    for i, (name, parent, mat) in enumerate(m.nodes):
        depth[i] = depth[parent] + 1 if parent >= 0 else 0
        tr = m.tracks.get(name)
        if tr is None:
            info = "静态 t=(%.2f %.2f %.2f)" % tuple(mat[3, :3])
        else:
            rot, posk, scl = tr
            info = "轨道 rot=%d pos=%d scl=%d  t0=(%.2f %.2f %.2f)" % (len(rot), len(posk), len(scl), *posk[0, 1:])
        print("%3d %s%s  %s" % (i, "  " * depth[i], name, info))


def cmd_check(d):
    files = sorted(glob.glob(os.path.join(d, "*.mtn")))
    ref = parse(files[0])
    ref_sig = [(n, p) for n, p, _ in ref.nodes]
    bad = []
    tails = []
    for f in files[1:]:
        m = parse(f)
        if [(n, p) for n, p, _ in m.nodes] != ref_sig:
            bad.append(os.path.basename(f))
        if m.tail:
            tails.append((os.path.basename(f), len(m.tail)))
    print("%d 个 .mtn，骨架树和 %s 一致的 %d 个" % (len(files), os.path.basename(files[0]), len(files) - len(bad)))
    for b in bad:
        print("  x " + b)
    for name, n in tails:
        print("  ~ %s 轨道之后有 %d B 残片（导出工具垃圾，引擎不读）" % (name, n))
    return 1 if bad else 0


def cmd_stats(d):
    """每根骨：局部 x 轴（骨头指向）相对第一帧的最大 / 平均偏角，以及骨头原点相对根骨的活动范围（网格单位）。"""
    files = sorted(glob.glob(os.path.join(d, "*.mtn")))
    ref = parse(files[0])
    names = ref.names
    axes = {n: [] for n in names}
    origins = {n: [] for n in names}
    for f in files:
        m = parse(f)
        if m.names != names:
            print("跳过骨架不同的 %s" % f)
            continue
        for t in frame_times(m):
            W = world_mats(m, t)
            root = W[names[0]][3, :3]
            for n in names:
                L = local_at(m, n, t)
                ax = L[0, :3] / max(np.linalg.norm(L[0, :3]), 1e-9)
                axes[n].append(ax)
                origins[n].append((W[n][3, :3] - root) / 2.8791)
    print("%-24s %7s %7s   %s" % ("骨头", "最大角", "平均角", "原点活动范围 x y z（网格单位，相对根骨）"))
    for n in names:
        a = np.array(axes[n])
        ang = np.degrees(np.arccos(np.clip(a @ a[0], -1, 1)))
        o = np.array(origins[n])
        r = o.max(0) - o.min(0)
        print("%-24s %7.1f %7.1f   %5.1f %5.1f %5.1f" % (n, ang.max(), ang.mean(), r[0], r[1], r[2]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=".mtn 解析 / 骨架树 / 运动统计")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("tree"); p.add_argument("mtn")
    p = sub.add_parser("check"); p.add_argument("dir")
    p = sub.add_parser("stats"); p.add_argument("dir")
    p = sub.add_parser("roundtrip"); p.add_argument("dirs", nargs="+")
    args = ap.parse_args(argv)
    if args.cmd == "tree":
        cmd_tree(args.mtn)
        return 0
    if args.cmd == "roundtrip":
        bad = 0
        for d in args.dirs:
            files = sorted(glob.glob(os.path.join(d, "*.mtn")))
            ok = 0
            for f in files:
                raw = open(f, "rb").read()
                if write(parse_bytes(raw, f)) == raw:
                    ok += 1
                else:
                    bad += 1
                    print("  x 不一致：%s" % f)
            print("%s：%d / %d 逐字节一致" % (d, ok, len(files)))
        return 1 if bad else 0
    if args.cmd == "check":
        return cmd_check(args.dir)
    if args.cmd == "stats":
        cmd_stats(args.dir)
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
