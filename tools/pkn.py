#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pkn.py —— 《炮炮火枪手》客户端资源包（`Pack\\*.pkn`）的读写工具。

把明文资源树 `game_patched\\Pack_develop` 打成客户端能直接读的加密卷
`game_patched\\Pack_publish\\*.pkn`，格式**完整复刻原版**（客户端读取代码一个字节不改，
`bshook.dll` 只把 `Pack\\` 重定向到 `Pack_publish\\`）。

    python tools/pkn.py pack                       # 增量打包（默认目录取 server/config.py）
    python tools/pkn.py pack --check               # 只判断有没有过期，过期退出码 1
    python tools/pkn.py pack --verify              # 打完把每一卷回读，和明文逐字节比
    python tools/pkn.py plan                       # 只打印卷的规划（哪个目录进哪一卷）
    python tools/pkn.py list  <卷.pkn>             # 打印卷头和条目表
    python tools/pkn.py unpack <卷|目录> --out <目录>
    python tools/pkn.py verify-tree <卷目录> <明文树>

## 容器格式（V0.3 §114；地址是脱壳镜像 re/BigShot_22524.img 的出处）

    卷文件 "Pack/<卷名>.pkn"   s = 这条路径串（客户端自己拼的，正斜杠）
    [0, H)             填充               H = 0x1e + (Σ s 的 UTF-16 码元) % 0x138   (0x560370)
    [H, +0x14+2·nlen)  卷头  SNOW(key1)   u32 0 │ u32 stamp │ u32 gap │ u32 count │ u32 nlen │ UTF-16 组名
    gap 字节           填充
    条目表 SNOW(key2)  count × { u32 nlen │ UTF-16 相对名 │ u32 flags │ u32 blk │ u32 size │ u32 stored │ 16B salt }
    对齐到 1KB         填充               D = align1K(表尾)                       (0x560743)
    数据区             条目在 D + blk·1024 起 stored 字节，按表序连续、各自 1KB 对齐

* SNOW 2.0（`server/snow.py`，V0.1 §28）：keysize 128、IV 0。★ 每个区域各起一条流，
  按整字连续处理、**越过逻辑末尾**（`Effects` 的 34 字节卷头要解 36 字节才对）。
* key1（卷头，0x5608b0）  key[j] = (lo(s[j%len]) + j) & 0xff
* key2（条目表，0x560920）key[j] = (lo(s[len-1-(j%len)]) * ((j%3)+2) + j) & 0xff
* key3（条目，0x5600c0）  key[j] = (((j%5) + salt[j&15] + 2) * lo(name[j%len]) + j) & 0xff，
  name = 组名 + "/" + 相对名（插入时 0x560a20 拼的全名）
* flags：0x1 zlib；0x2 SNOW(key3) 整条；0x4 SNOW(key3) **只前 0x400 字节**。
  解码顺序：先 SNOW 再 inflate，最后截到 size。带 SNOW 的条目 stored 一律取整到 4。
* 客户端查找不分大小写（0x402c57 逐字符过 0x5f43d4），条目名按 `/` 分。
* 卷头里的 stamp 客户端写进 vol+0x18 后没有任何读取；所有填充区它也不碰。

## 打包策略（写入侧自己定的，客户端不关心）

* **一个子目录一卷**：`Maps/swamp/**` → `Maps~swamp.pkn`；组根下的散文件 → `Maps~.pkn`；
  目录超过 `VOLUME_CAP` 且有子目录时再往下一级拆（本目录散文件进 `Maps~x~.pkn`）；
  叶子目录不拆。改一个文件只重写它所在的那一小卷。
* **确定性**：条目按名排、填充写 0、salt / gap / stamp 都由名字和内容派生 ——
  同样的明文打两次，字节相同；git 里不会出现「没改也变了」的卷。
* **增量**：`pack-index.json` 记每卷的成员 (名, 大小, sha256)，成员没变、卷文件也没变
  就不重写 —— 换台机器 zlib 版本不同也不会无谓重写（zlib 输出只在真重写时才可能不同）。

★ 3.8 兼容（`runtime-win7` 那套也要能 import）。只用标准库。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if os.path.join(ROOT, "server") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "server"))

import snow                      # noqa: E402
import config as server_config   # noqa: E402

# ---------------------------------------------------------------------------
#  常量
# ---------------------------------------------------------------------------

#: 客户端派生密钥用的路径前缀 = 原版目录名 + "/"。**运行时读的是 Pack_publish，
#: 但客户端内部拼的串仍是 "Pack/…"**（hook 在 API 层改路径），所以这里永远是 "Pack/"。
KEY_PREFIX = server_config.PACK_LEGACY_DIR + "/"

DEFAULT_SRC = os.path.join(ROOT, "game_patched", server_config.PACK_DEVELOP_DIR)
DEFAULT_OUT = os.path.join(ROOT, "game_patched", server_config.PACK_PUBLISH_DIR)

#: sidecar 清单（增量判据）。进 git、随卷进包（客户端只挂 *.pkn，无副作用）。
INDEX_NAME = "pack-index.json"
INDEX_FORMAT = 1

#: 一卷的软上限。目录超过它且还有子目录就往下一级拆；叶子目录不拆（可以超）。
VOLUME_CAP = 16 * 1024 * 1024
#: 卷名里目录层级的分隔符。原版资源的目录名里没有它（打包器会 assert）。
VOLUME_SEP = "~"
#: 填充字节。读取器不碰填充，0 让 git 对象更小。
FILL_BYTE = 0
ZLIB_LEVEL = 9
#: 策略指纹：任何一项变了，所有卷都算过期（写进 sidecar，对不上就整体重打）。
POLICY_VERSION = "original-ext-v1"

#: flags 按扩展名定 —— 照 82 卷原版统计（16266 条零例外）。`.bak` / `.rNNNNN`
#: 这类 svn 后缀先剥掉再看。表里没有的扩展名走 DEFAULT_FLAGS。
FLAGS_BY_EXT = {
    ".dds": 1, ".mtn": 1, ".smf": 1, ".txt": 1,
    ".efx": 3, ".evn": 3, ".map": 3, ".ini": 3, ".xml": 3,
    ".png": 4, ".ogg": 4, ".jpg": 4, ".tga": 4,
    ".msh": 2, ".ui": 2, ".amf": 2,
    ".uni": 0, ".bmp": 0, ".csv": 0, ".zip": 0, ".wav": 0, "": 0,
}
DEFAULT_FLAGS = 3

FLAG_ZLIB = 0x1
FLAG_SNOW_ALL = 0x2
FLAG_SNOW_HEAD = 0x4
SNOW_HEAD_BYTES = 0x400

#: 明文树里这些东西不是资源，跳过并告警（编辑器 / 资源管理器留下的）。
IGNORE_NAMES = {"thumbs.db", "desktop.ini"}
IGNORE_SUFFIXES = (".tmp", "~")

HEADER_FIXED = 0x14          # 5 个 u32
ENTRY_FIXED = 16 + 16        # 4 个 u32 + 16 字节 salt
BLOCK = 1024


def ceil4(n):
    return (n + 3) & ~3


def align_block(n):
    return (n + BLOCK - 1) & ~(BLOCK - 1)


# ---------------------------------------------------------------------------
#  密钥 / 偏移派生（都只取宽字符的低字节，和客户端一样）
# ---------------------------------------------------------------------------

def header_offset(key_path):
    """卷头在文件里的偏移（0x560370：路径串 UTF-16 码元之和 % 0x138 + 0x1e）。"""
    return 0x1E + (sum(ord(c) for c in key_path) % 0x138)


def key1(key_path):
    """卷头密钥（0x5608b0）。"""
    n = len(key_path)
    return bytes(((ord(key_path[j % n]) & 0xFF) + j) & 0xFF for j in range(16))


def key2(key_path):
    """条目表密钥（0x560920）：路径串倒着取。"""
    n = len(key_path)
    return bytes((((ord(key_path[n - 1 - (j % n)]) & 0xFF) * ((j % 3) + 2)) + j) & 0xFF
                 for j in range(16))


def key3(full_name, salt):
    """条目密钥（0x5600c0）：全名（组名/相对名）+ 条目自己的 16 字节 salt。"""
    n = len(full_name)
    return bytes(((((j % 5) + salt[j & 15] + 2) * (ord(full_name[j % n]) & 0xFF)) + j) & 0xFF
                 for j in range(16))


def volume_key_path(volume_filename):
    return KEY_PREFIX + volume_filename


def flags_for(rel_name):
    """按扩展名定 flags。先剥 svn 那种 `.rNNNNN` / `.bak` 后缀。"""
    base = rel_name.rsplit("/", 1)[-1].lower()
    while True:
        stem, dot, ext = base.rpartition(".")
        if not dot:
            return FLAGS_BY_EXT.get("", DEFAULT_FLAGS)
        ext = "." + ext
        if ext == ".bak" or (len(ext) > 2 and ext[1] == "r" and ext[2:].isdigit()):
            base = stem
            if "." not in base:
                return DEFAULT_FLAGS
            continue
        return FLAGS_BY_EXT.get(ext, DEFAULT_FLAGS)


# ---------------------------------------------------------------------------
#  读取器
# ---------------------------------------------------------------------------

class PknError(Exception):
    pass


class Entry:
    __slots__ = ("name", "flags", "blk", "size", "stored", "salt")

    def __init__(self, name, flags, blk, size, stored, salt):
        self.name = name
        self.flags = flags
        self.blk = blk
        self.size = size
        self.stored = stored
        self.salt = salt

    def __repr__(self):
        return "Entry(%r, flags=%d, blk=%d, size=%d, stored=%d)" % (
            self.name, self.flags, self.blk, self.size, self.stored)


class Volume:
    """一卷。构造时把卷头和条目表解开；条目数据按需解。"""

    def __init__(self, path, key_path=None):
        self.path = path
        self.filename = os.path.basename(path)
        self.key_path = key_path or volume_key_path(self.filename)
        with open(path, "rb") as f:
            self.data = f.read()
        self._parse()

    def _parse(self):
        data = self.data
        H = header_offset(self.key_path)
        if H + HEADER_FIXED > len(data):
            raise PknError("%s: 文件太短，放不下卷头" % self.filename)
        c1 = snow.Snow(key1(self.key_path))
        fixed = c1.decrypt(data[H:H + HEADER_FIXED])
        zero, stamp, gap, count, nlen = struct.unpack_from("<5I", fixed, 0)
        if zero != 0 or nlen > 260 or count > 1_000_000:
            raise PknError("%s: 卷头解不开（密钥前缀 %r 对不对？）" % (self.filename, KEY_PREFIX))
        # 组名紧跟 5 个 u32；同一条 SNOW 流按整字继续（ceil4）。
        name_bytes = c1.decrypt(data[H + HEADER_FIXED:H + HEADER_FIXED + ceil4(2 * nlen)])
        self.root = name_bytes[:2 * nlen].decode("utf-16-le")
        self.stamp = stamp
        self.gap = gap
        self.header_offset = H
        T = H + HEADER_FIXED + 2 * nlen + gap
        self.table_offset = T
        self.entries = self._parse_table(T, count)
        table_len = self._table_len
        self.data_offset = align_block(T + table_len)
        self.count = count

    def _parse_table(self, T, count):
        """条目表：长度没存，按 count 顺序读。密文流连续，按整字分块解。"""
        data = self.data
        c2 = snow.Snow(key2(self.key_path))
        buf = bytearray()
        pos = T           # 下一段密文的文件偏移（4 对齐地推进）
        off = 0           # 已解明文里消费到哪

        def need(n):
            nonlocal pos
            while len(buf) - off < n:
                chunk = min(65536, len(data) - pos) & ~3
                if chunk <= 0:
                    raise PknError("%s: 条目表在文件尾之前没读完" % self.filename)
                buf.extend(c2.decrypt(data[pos:pos + chunk]))
                pos += chunk

        entries = []
        for _ in range(count):
            need(4)
            (nlen,) = struct.unpack_from("<I", buf, off)
            off += 4
            if nlen > 1024:
                raise PknError("%s: 条目名长度 %d 不像话，条目表解坏了" % (self.filename, nlen))
            need(2 * nlen + ENTRY_FIXED)
            name = bytes(buf[off:off + 2 * nlen]).decode("utf-16-le")
            off += 2 * nlen
            flags, blk, size, stored = struct.unpack_from("<4I", buf, off)
            off += 16
            salt = bytes(buf[off:off + 16])
            off += 16
            entries.append(Entry(name, flags, blk, size, stored, salt))
        self._table_len = off
        return entries

    def full_name(self, entry):
        return self.root + "/" + entry.name

    def read_stored(self, entry):
        a = self.data_offset + entry.blk * BLOCK
        if a + entry.stored > len(self.data):
            raise PknError("%s: 条目 %s 越过文件尾" % (self.filename, entry.name))
        return self.data[a:a + entry.stored]

    def read(self, entry):
        """还原一个条目的明文（先 SNOW 再 inflate，最后截到 size）。"""
        raw = self.read_stored(entry)
        flags = entry.flags
        if flags & FLAG_SNOW_HEAD:
            k = key3(self.full_name(entry), entry.salt)
            n = min(SNOW_HEAD_BYTES, len(raw))
            raw = snow.Snow(k).decrypt(raw[:n]) + raw[n:]
        elif flags & FLAG_SNOW_ALL:
            k = key3(self.full_name(entry), entry.salt)
            raw = snow.Snow(k).decrypt(raw)
        if flags & FLAG_ZLIB:
            d = zlib.decompressobj()
            out = d.decompress(raw)
            if not d.eof:
                raise PknError("%s: 条目 %s 的 zlib 流不完整" % (self.filename, entry.name))
            raw = out
        return raw[:entry.size]

    def describe(self):
        lines = ["%s  key_path=%r  H=0x%x  stamp=0x%08x  gap=0x%x  root=%r  count=%d  D=0x%x  size=%d"
                 % (self.filename, self.key_path, self.header_offset, self.stamp, self.gap,
                    self.root, self.count, self.data_offset, len(self.data))]
        for e in self.entries:
            lines.append("  flags=%d blk=%6d size=%9d stored=%9d salt=%s  %s"
                         % (e.flags, e.blk, e.size, e.stored, e.salt.hex(), e.name))
        return "\n".join(lines)


def iter_volume_files(path):
    """`path` 是一个 .pkn 或一个目录；目录时按名字序给出里面所有 .pkn。"""
    if os.path.isdir(path):
        names = sorted(n for n in os.listdir(path) if n.lower().endswith(".pkn"))
        return [os.path.join(path, n) for n in names]
    return [path]


# ---------------------------------------------------------------------------
#  写入器
# ---------------------------------------------------------------------------

def _derive_u32(tag, name):
    return int.from_bytes(hashlib.sha256((tag + "|" + name).encode("utf-8")).digest()[:4], "little")


def derive_stamp(volume_filename):
    """卷头第二个 u32。客户端不读它；原版观测值低 4 位恒 0，照这个样子给一个确定值。"""
    return _derive_u32("stamp", volume_filename) & 0xFFFFFFF0


def derive_gap(volume_filename):
    """卷头和条目表之间的空隙长度。原版观测范围 0x30..0xe5。"""
    return 0x30 + _derive_u32("gap", volume_filename) % (0xE5 - 0x30 + 1)


def derive_salt(full_name, content):
    h = hashlib.sha256()
    h.update(full_name.encode("utf-16-le"))
    h.update(b"\0\0")
    h.update(content)
    return h.digest()[:16]


def _fill(n):
    return bytes([FILL_BYTE]) * n


def encode_entry(full_name, rel_name, content):
    """一个条目在数据区里的字节（已加密）+ 表项字段。返回 (payload, flags, size, stored, salt)。"""
    flags = flags_for(rel_name)
    payload = zlib.compress(content, ZLIB_LEVEL) if flags & FLAG_ZLIB else content
    if flags & (FLAG_SNOW_ALL | FLAG_SNOW_HEAD):
        payload = payload + _fill(ceil4(len(payload)) - len(payload))
    salt = derive_salt(full_name, content)
    if flags & FLAG_SNOW_HEAD:
        n = min(SNOW_HEAD_BYTES, len(payload))
        payload = snow.Snow(key3(full_name, salt)).encrypt(payload[:n]) + payload[n:]
    elif flags & FLAG_SNOW_ALL:
        payload = snow.Snow(key3(full_name, salt)).encrypt(payload)
    return payload, flags, len(content), len(payload), salt


def build_volume(volume_filename, root, members):
    """把 members（[(相对名, 明文字节)]，任意顺序）打成一卷的完整字节。纯函数、确定性。"""
    key_path = volume_key_path(volume_filename)
    members = sorted(members, key=lambda m: m[0])
    if not members:
        raise PknError("%s: 空卷没有意义" % volume_filename)
    seen = set()
    encoded = []
    blk = 0
    for rel_name, content in members:
        low = rel_name.lower()
        if low in seen:
            raise PknError("%s: 条目 %r 不分大小写重名（客户端查找不分大小写）"
                           % (volume_filename, rel_name))
        seen.add(low)
        full = root + "/" + rel_name
        payload, flags, size, stored, salt = encode_entry(full, rel_name, content)
        encoded.append((rel_name, payload, flags, blk, size, stored, salt))
        blk += (stored + BLOCK - 1) // BLOCK

    table = bytearray()
    for rel_name, payload, flags, eblk, size, stored, salt in encoded:
        nb = rel_name.encode("utf-16-le")
        table += struct.pack("<I", len(nb) // 2) + nb
        table += struct.pack("<4I", flags, eblk, size, stored) + salt
    table = bytes(table)

    H = header_offset(key_path)
    gap = derive_gap(volume_filename)
    hdr_len = HEADER_FIXED + 2 * len(root)
    # 表尾距离下一个 1KB 边界不能少于 4 字节：连续密文流会多加密 1~3 个字节的填充，
    # 无论客户端按逻辑末尾还是按密文末尾对齐，得到的数据区起点都要一样。
    while ((H + hdr_len + gap + len(table)) & (BLOCK - 1)) > BLOCK - 4:
        gap += 4
    T = H + hdr_len + gap
    D = align_block(T + len(table))

    header = struct.pack("<5I", 0, derive_stamp(volume_filename), gap, len(encoded), len(root))
    header += root.encode("utf-16-le")
    hdr_region = header + _fill(ceil4(len(header)) - len(header))
    tab_region = table + _fill(ceil4(len(table)) - len(table))

    total = D + blk * BLOCK          # 末条也补到 1KB，和原版一样
    out = bytearray(_fill(total))
    out[H:H + len(hdr_region)] = snow.Snow(key1(key_path)).encrypt(hdr_region)
    out[T:T + len(tab_region)] = snow.Snow(key2(key_path)).encrypt(tab_region)
    for rel_name, payload, flags, eblk, size, stored, salt in encoded:
        a = D + eblk * BLOCK
        out[a:a + len(payload)] = payload
    return bytes(out)


# ---------------------------------------------------------------------------
#  明文树扫描 / 卷规划
# ---------------------------------------------------------------------------

_SAFE_DIR_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
                      "_-.!#$%&'()+,;=@[]^{} ")


def _ignored(name):
    low = name.lower()
    return name.startswith(".") or low in IGNORE_NAMES or any(low.endswith(s) for s in IGNORE_SUFFIXES)


def scan_tree(src):
    """遍历明文树。返回 (files, warnings)：files = {组名: {相对名: (绝对路径, 大小)}}。

    组 = 顶层目录（Data / Effects / …）；相对名相对组根、`/` 分隔、保留大小写。
    顶层的散文件不属于任何组，告警并跳过。
    """
    files = {}
    warnings = []
    if not os.path.isdir(src):
        raise PknError("明文树不存在：%s" % src)
    for group in sorted(os.listdir(src)):
        gpath = os.path.join(src, group)
        if not os.path.isdir(gpath):
            if not _ignored(group):
                warnings.append("顶层散文件不进任何卷，跳过：%s" % group)
            continue
        if _ignored(group):
            warnings.append("跳过目录：%s" % group)
            continue
        table = {}
        for dirpath, dirnames, filenames in os.walk(gpath):
            dirnames[:] = sorted(d for d in dirnames if not _ignored(d))
            rel_dir = os.path.relpath(dirpath, gpath).replace(os.sep, "/")
            if rel_dir == ".":
                rel_dir = ""
            for fn in sorted(filenames):
                if _ignored(fn):
                    warnings.append("跳过：%s/%s%s" % (group, rel_dir + "/" if rel_dir else "", fn))
                    continue
                rel = (rel_dir + "/" + fn) if rel_dir else fn
                full = os.path.join(dirpath, fn)
                table[rel] = (full, os.path.getsize(full))
        if table:
            files[group] = table
    return files, warnings


def _volume_name(group, dir_parts, own_only):
    parts = [group] + list(dir_parts)
    for p in parts:
        bad = [c for c in p if c not in _SAFE_DIR_CHARS]
        if bad or VOLUME_SEP in p:
            raise PknError("目录名 %r 含卷名里不能用的字符 %r —— 改个名字再打包" % (p, "".join(bad) or VOLUME_SEP))
    name = VOLUME_SEP.join(parts)
    if own_only:
        name += VOLUME_SEP
    return name + ".pkn"


def assign_volumes(files, cap=VOLUME_CAP):
    """卷规划。返回 [(卷文件名, 组名, 来源目录, [相对名…])]，按卷名排序。纯函数。"""
    plans = []
    for group, table in files.items():
        # 目录树：{目录相对路径: (散文件名列表, 子目录集合, 子树总大小)}
        own = {}
        subs = {}
        total = {}
        for rel, (_p, size) in table.items():
            d, _s, fn = rel.rpartition("/")
            own.setdefault(d, []).append(rel)
            # 逐级累加大小、登记父子关系
            cur = d
            while True:
                total[cur] = total.get(cur, 0) + size
                if cur == "":
                    break
                parent, _s2, _leaf = cur.rpartition("/")
                subs.setdefault(parent, set()).add(cur)
                cur = parent
            subs.setdefault(d, set())
        subs.setdefault("", set())

        def emit(d):
            parts = d.split("/") if d else []
            children = sorted(subs.get(d, ()))
            if d == "":
                if own.get(""):
                    plans.append((_volume_name(group, [], True), group, group, sorted(own[""])))
                for c in children:
                    emit(c)
                return
            if total.get(d, 0) <= cap or not children:
                prefix = d + "/"
                mem = sorted(r for r in table if r.startswith(prefix))
                plans.append((_volume_name(group, parts, False), group, group + "/" + d, mem))
                return
            if own.get(d):
                plans.append((_volume_name(group, parts, True), group, group + "/" + d, sorted(own[d])))
            for c in children:
                emit(c)

        emit("")
    plans.sort(key=lambda p: p[0].lower())
    names = [p[0].lower() for p in plans]
    if len(names) != len(set(names)):
        dup = sorted({n for n in names if names.count(n) > 1})
        raise PknError("卷名撞车：%s" % ", ".join(dup))
    return plans


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_hash(member_hashes):
    """整棵明文树的哈希：所有「组/相对名 sha256」按名排序后再 sha256。"""
    h = hashlib.sha256()
    for full, digest in sorted(member_hashes.items()):
        h.update(full.encode("utf-8") + b"\t" + digest.encode("ascii") + b"\n")
    return h.hexdigest()


def policy_fingerprint():
    return {
        "policy": POLICY_VERSION,
        "zlib_level": ZLIB_LEVEL,
        "volume_cap": VOLUME_CAP,
        "key_prefix": KEY_PREFIX,
        "sep": VOLUME_SEP,
        "fill": FILL_BYTE,
    }


def load_index(out_dir):
    path = os.path.join(out_dir, INDEX_NAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(obj, dict) or obj.get("format") != INDEX_FORMAT:
        return None
    return obj


def dump_index(out_dir, obj):
    """写 sidecar。**一成员一行**：git diff 一眼能看出改了哪个文件；键排序保证确定性。

    `json.dumps(indent=…)` 会把成员的三个字段拆成三行（16266 条 = 8 万行），所以
    外层手工排版、每条成员用紧凑 JSON 单独一行。读的一侧照旧 `json.load`。
    """
    def j(x):
        return json.dumps(x, ensure_ascii=False, sort_keys=True)

    lines = ["{", ' "format": %s,' % j(obj["format"]), ' "packer": %s,' % j(obj["packer"]),
             ' "tree_hash": %s,' % j(obj["tree_hash"]), ' "volumes": {']
    vols = obj["volumes"]
    for i, name in enumerate(sorted(vols)):
        v = vols[name]
        head = {k: v[k] for k in v if k != "members"}
        lines.append('  %s: {%s, "members": [' % (j(name), j(head)[1:-1]))
        members = v["members"]
        for k, m in enumerate(members):
            lines.append("   " + j(m) + ("," if k + 1 < len(members) else ""))
        lines.append("  ]}" + ("," if i + 1 < len(vols) else ""))
    lines += [" }", "}", ""]
    path = os.path.join(out_dir, INDEX_NAME)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    os.replace(tmp, path)


class PackResult:
    def __init__(self):
        self.written = []
        self.deleted = []
        self.unchanged = 0
        self.tree_hash = ""
        self.warnings = []
        self.volumes = 0
        self.index_stale = False     # 清单不在 / 策略变了 / 记的树哈希对不上

    @property
    def changed(self):
        return bool(self.written or self.deleted or self.index_stale)

    def to_json(self):
        return {"written": self.written, "deleted": self.deleted, "unchanged": self.unchanged,
                "tree_hash": self.tree_hash, "volumes": self.volumes, "changed": self.changed,
                "index_stale": self.index_stale, "warnings": self.warnings}


def _assert_writable(paths):
    """游戏开着时卷被内存映射，改名 / 删除必败。先试着以写方式打开，有一个不行就整体不动。"""
    locked = []
    for p in paths:
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r+b"):
                pass
        except OSError:
            locked.append(os.path.basename(p))
    if locked:
        raise PknError("这些卷正被占用（游戏开着？先 stop.bat）：%s" % ", ".join(locked[:5])
                       + ("…" if len(locked) > 5 else ""))


def pack(src=DEFAULT_SRC, out=DEFAULT_OUT, force=False, check=False, cap=VOLUME_CAP, log=print):
    """增量打包。check=True 时只判断，不写任何东西（过期 -> PackError 之外的返回值 changed）。"""
    files, warnings = scan_tree(src)
    for w in warnings:
        log("[pack] ! " + w)
    plans = assign_volumes(files, cap)
    if not plans:
        raise PknError("明文树 %s 里没有任何资源" % src)

    # 逐文件哈希（几百 MB，磁盘热的时候一两秒）
    hashes = {}
    for group, table in files.items():
        for rel, (path, _size) in table.items():
            hashes[group + "/" + rel] = sha256_file(path)

    res = PackResult()
    res.warnings = warnings
    res.tree_hash = tree_hash(hashes)
    res.volumes = len(plans)

    old = None if force else load_index(out)
    fingerprint = policy_fingerprint()
    old_vols = {}
    if old and old.get("packer") == fingerprint:
        old_vols = old.get("volumes", {})

    new_vols = {}
    to_write = []
    for vol, group, source, members in plans:
        table = files[group]
        mem_rec = [[rel, table[rel][1], hashes[group + "/" + rel]] for rel in members]
        rec = {"group": group, "source": source, "members": mem_rec}
        prev = old_vols.get(vol)
        vpath = os.path.join(out, vol)
        fresh = False
        if prev and prev.get("group") == group and prev.get("members") == mem_rec \
                and os.path.isfile(vpath) and os.path.getsize(vpath) == prev.get("size") \
                and sha256_file(vpath) == prev.get("sha256"):
            fresh = True
            rec["size"] = prev["size"]
            rec["sha256"] = prev["sha256"]
        new_vols[vol] = rec
        if fresh:
            res.unchanged += 1
        else:
            to_write.append((vol, group, members))

    planned = {v.lower() for v in new_vols}
    stale = []
    if os.path.isdir(out):
        for n in os.listdir(out):
            low = n.lower()
            if (low.endswith(".pkn") and low not in planned) or low.endswith(".pkn.tmp"):
                stale.append(n)
    res.written = [v for v, _g, _m in to_write]
    res.deleted = sorted(stale)
    res.index_stale = (old is None or old.get("packer") != fingerprint
                       or old.get("tree_hash") != res.tree_hash)

    if check or not res.changed:
        return res

    os.makedirs(out, exist_ok=True)
    _assert_writable([os.path.join(out, v) for v in res.written] + [os.path.join(out, n) for n in stale])

    for n in stale:
        os.remove(os.path.join(out, n))
        log("[pack] 删掉不再对应任何目录的卷：%s" % n)
    for vol, group, members in to_write:
        table = files[group]
        blobs = []
        for rel in members:
            with open(table[rel][0], "rb") as f:
                blobs.append((rel, f.read()))
        data = build_volume(vol, group, blobs)
        vpath = os.path.join(out, vol)
        tmp = vpath + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, vpath)
        new_vols[vol]["size"] = len(data)
        new_vols[vol]["sha256"] = sha256_bytes(data)
        log("[pack] 写出 %-40s %3d 个文件 %9d 字节" % (vol, len(members), len(data)))

    dump_index(out, {"format": INDEX_FORMAT, "packer": fingerprint,
                     "tree_hash": res.tree_hash, "volumes": new_vols})
    return res


def verify(src, out, log=print):
    """把 out 里每一卷回读，和 src 里的明文逐字节比。返回 (对上的, 差异清单)。"""
    files, _w = scan_tree(src)
    expected = {}
    for group, table in files.items():
        for rel, (path, _size) in table.items():
            expected[group + "/" + rel] = path
    ok = 0
    problems = []
    seen = set()
    for vpath in iter_volume_files(out):
        v = Volume(vpath)
        for e in v.entries:
            full = v.full_name(e)
            seen.add(full)
            ref = expected.get(full)
            if ref is None:
                problems.append("%s: 多出来的条目 %s" % (v.filename, full))
                continue
            with open(ref, "rb") as f:
                want = f.read()
            got = v.read(e)
            if got == want:
                ok += 1
            else:
                problems.append("%s: %s 内容不一致（%d vs %d 字节）" % (v.filename, full, len(got), len(want)))
    for full in sorted(set(expected) - seen):
        problems.append("明文里有、卷里没有：%s" % full)
    return ok, problems


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------

def cmd_list(args):
    for p in iter_volume_files(args.volume):
        print(Volume(p).describe())
    return 0


def cmd_unpack(args):
    total = 0
    for p in iter_volume_files(args.volume):
        v = Volume(p)
        for e in v.entries:
            dest = os.path.join(args.out, v.root, *e.name.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(v.read(e))
            total += 1
        print("[unpack] %-20s %5d 个文件 -> %s/%s" % (v.filename, len(v.entries), args.out, v.root))
    print("[unpack] 共 %d 个文件" % total)
    return 0


def cmd_verify_tree(args):
    ok, problems = verify(args.tree, args.volumes)
    for p in problems:
        print("[verify] !! " + p)
    print("[verify] 对上 %d 个文件，差异 %d 处" % (ok, len(problems)))
    return 1 if problems else 0


def cmd_plan(args):
    files, warnings = scan_tree(args.src)
    for w in warnings:
        print("! " + w)
    plans = assign_volumes(files, args.cap)
    total = 0
    for vol, group, source, members in plans:
        size = sum(files[group][r][1] for r in members)
        total += size
        print("%-48s %5d 个文件 %9.2f MB  <- %s" % (vol, len(members), size / 1e6, source))
    print("共 %d 卷，%.1f MB 明文" % (len(plans), total / 1e6))
    return 0


#: 「服务端那五份数据是从哪一版明文树提取的」—— 进 git、不进包。build-pack.ps1 拿它
#: 和当前明文树比：不等就跑 update-gamedata，成功后由 update-gamedata.ps1 写回。
#: 判据是树哈希这个事实，不是时间戳；提取失败戳不更新，下次 build-pack 会再试。
GAMEDATA_STAMP = os.path.join(HERE, "gamedata-stamp.json")


def current_tree_hash(src):
    files, _w = scan_tree(src)
    hashes = {}
    for group, table in files.items():
        for rel, (path, _size) in table.items():
            hashes[group + "/" + rel] = sha256_file(path)
    return tree_hash(hashes)


def cmd_gamedata_stamp(args):
    now = current_tree_hash(args.src)
    if args.write:
        with open(GAMEDATA_STAMP, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"pack_tree_hash": now, "source": os.path.relpath(args.src, ROOT).replace(os.sep, "/")},
                      f, indent=1, sort_keys=True)
            f.write("\n")
        print("[stamp] 已记下：服务端数据对应明文树 %s" % now[:12])
        return 0
    try:
        with open(GAMEDATA_STAMP, "r", encoding="utf-8") as f:
            have = json.load(f).get("pack_tree_hash")
    except (OSError, ValueError):
        have = None
    if have == now:
        print("[stamp] 服务端数据和明文树 %s 对得上" % now[:12])
        return 0
    print("[stamp] 服务端数据过期：戳 %s，明文树 %s —— 要跑 update-gamedata"
          % ((have or "(没有)")[:12], now[:12]))
    return 1


def cmd_pack(args):
    try:
        res = pack(args.src, args.out, force=args.force, check=args.check, cap=args.cap)
    except PknError as e:
        print("[pack] !! %s" % e)
        return 1
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(res.to_json(), f, ensure_ascii=False, indent=1)
    if args.check:
        if res.changed:
            print("[pack] 过期：要重写 %d 卷、删 %d 卷（%s）" % (
                len(res.written), len(res.deleted), ", ".join((res.written + res.deleted)[:6])))
            return 1
        print("[pack] %d 卷都是最新的" % res.volumes)
        return 0
    print("[pack] 共 %d 卷：重写 %d、删除 %d、未变 %d；tree=%s"
          % (res.volumes, len(res.written), len(res.deleted), res.unchanged, res.tree_hash[:12]))
    if args.verify:
        ok, problems = verify(args.src, args.out)
        for p in problems:
            print("[verify] !! " + p)
        print("[verify] 对上 %d 个文件，差异 %d 处" % (ok, len(problems)))
        if problems:
            return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="炮炮火枪手 pkn 资源包读写工具")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("list", help="打印卷头和条目表")
    p.add_argument("volume")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("unpack", help="解开一卷或一个目录里的全部卷")
    p.add_argument("volume")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_unpack)

    p = sub.add_parser("verify-tree", help="把卷目录整个回读，和明文树逐字节比")
    p.add_argument("volumes")
    p.add_argument("tree")
    p.set_defaults(func=cmd_verify_tree)

    p = sub.add_parser("plan", help="只打印卷规划")
    p.add_argument("--src", default=DEFAULT_SRC)
    p.add_argument("--cap", type=int, default=VOLUME_CAP)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("gamedata-stamp",
                       help="服务端五份数据对应的明文树哈希：--write 记下，不带参数则校验（过期退出码 1）")
    p.add_argument("--src", default=DEFAULT_SRC)
    p.add_argument("--write", action="store_true")
    p.set_defaults(func=cmd_gamedata_stamp)

    p = sub.add_parser("pack", help="增量打包 Pack_develop -> Pack_publish")
    p.add_argument("--src", default=DEFAULT_SRC)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--cap", type=int, default=VOLUME_CAP)
    p.add_argument("--force", action="store_true", help="无视清单，全部重写")
    p.add_argument("--check", action="store_true", help="只判断是否过期（过期退出码 1），不写")
    p.add_argument("--verify", action="store_true", help="打完把每卷回读和明文逐字节比")
    p.add_argument("--report", help="把结果写成 JSON（build-pack.ps1 读它）")
    p.set_defaults(func=cmd_pack)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    # ★ 自己把 stdout / stderr 钉死成 utf-8 —— 消息是中文、条目名里还有韩文。
    #
    # 不钉的话：调用方一旦**捕获**输出（`build-common.ps1` 的 `| Out-Host`、或者
    # 赋值给变量），CPython 就发现 stdout 不是控制台，改用 `GetACP()` = cp936 编码
    # （`chcp 65001` 改的是控制台代码页，**改不了 GetACP()**）；而入口 bat 的
    # `chcp 65001` 让 PowerShell 按 utf-8 去解那串 GBK 字节 —— 一个汉字碎成两个
    # 替换符，韩文直接编不出来。这就是 2026-09-14 那次 build-pack 满屏乱码。
    # 只补 `errors="replace"` 挡不住：它治的是「编不出来」，不治「编错了」。
    #
    # 走控制台那条路本来就是 utf-8（`_WindowsConsoleIO` 强制），所以两条路一致，
    # 不管谁来调（PowerShell / cmd / CI / 重定向到文件）都对。`errors` 留着兜意外。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        return args.func(args)
    except PknError as e:
        print("!! %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
