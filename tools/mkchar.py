#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mkchar.py —— 把一个角色的整套模型克隆成另一个角色（X_Mod · X1 爱琳）。

    python tools/mkchar.py --from ch01 --to ch03 --dry-run
    python tools/mkchar.py --from ch01 --to ch03
    python tools/mkchar.py --audit ch03

## 为什么要克隆（D1）

爱琳（`ChrIndex=3`）在 311 客户端里**只差 `Models/Characters/ch03/`**：属性表、
中文名、武器表、特效、选人语音、战斗头像原画全都在（FINDINGS §1）。
本仓库没有 `.msh` 写出器也没有建模软件，所以第一版走「整套克隆 ch01 卡希尔 + 改皮」。

**必须同源**：`.msh` 和 `.mtn` 都来自同一个角色 —— 骨骼是**按名字**在角色骨架里找的，
找不到返 NULL，喂进矩阵乘直接崩（V0.3商店 §50）。

## 三个坑（FINDINGS §7）

1. `.msh` 里的贴图名是 `u32 码元数 + UTF-16LE`，要跟着改名；但**偏移 0x4c 还有一个
   长度前缀串是挂接特效路径**（`ArmorSpakle\\Efx\\CH01_*.efx`），`Effects/ArmorSpakle/`
   下没有 CH03 版本，**一改就断链**。
   ⇒ 本脚本只替换「**恰好等于源目录里某个 .dds 文件名**」的串，路径串天然匹配不上。
2. `.mtn` 内部 0 处 `chNN` 串 ⇒ **一个字节都不改**。
3. `.evn` 里有 **2 个引用别的角色**（`Mutu-DamagedFly`→ch00、`MutuStand-P00`→ch02），
   而且 CH03 的特效只有 102 个、命名规则也和 CH01 不一样 ⇒ **默认一个字都不改**，
   让爱琳沿用卡希尔的动作音效和尘土特效。缺音频不会崩（§4）。

## 别名

`weapon.ini` 的 `[ch03-03]` / `[ch03-03a]` 点名要 `Characters/ch03/ch03D0003A`，
而 ch01 只有 `D0003` 没有 `D0003A`。
⇒ 额外拷一份**字节完全相同**的 `ch03D0003A.msh`（它内部指向的贴图仍是 `ch03D0003.dds`，
那个文件在）。**不改长度前缀就不会动到文件里任何偏移。**

## 幂等

先算出「完整目标文件集」，再逐个和磁盘做字节比较，**只写不同的**。
第二次跑输出「0 写入」。不写时间戳、遍历排序 ⇒ 打包器的增量判据不会无谓翻。
"""
from __future__ import annotations

import argparse
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACK = os.path.join(ROOT, "game_patched", "Pack_develop")
CHARS = os.path.join(PACK, "Models", "Characters")
DATA = os.path.join(PACK, "Data")

#: 角色目录里允许出现的扩展名。见到别的一律报错退出 ——
#: 宁可停下，也不要把不认识的东西塞进发布卷里。
KNOWN_EXT = {".msh", ".dds", ".mtn", ".evn"}

#: 额外别名：`目标文件名 -> 同目录里的源文件名`（字节原样复制，不改内容）。
ALIASES = {
    ("ch01", "ch03"): {"ch03D0003A.msh": "ch03D0003.msh"},
}

#: 大头像图集至少要有这么多帧，否则爱琳的 0x1a/0x1b 越界，**一进大厅就崩**（§3）。
ICON_SMF = os.path.join(PACK, "Images", "Chinese", "BigChrIcons_CN.smf")
ICON_MIN_FRAMES = 28

#: 从一段可打印文本里抠出「像文件名」的片段。审计时要求它在目标目录里真实存在。
_BARE_FILE = re.compile(r"[A-Za-z0-9_^\-]+\.(?:dds|msh|efx|ogg|png)", re.I)

#: 「整段恰好是一条资源路径」。★ 必须**整段匹配**：顶点和索引数据里经常偶然凑出
#: 带 `/` 或 `\` 的可打印段，不卡死就会在报告里刷一屏二进制垃圾（2026-09-15 实测）。
_EXT_PATH = re.compile(
    r"^[A-Za-z0-9_][A-Za-z0-9_ .\-]*(?:[\\/][A-Za-z0-9_][A-Za-z0-9_ .\-]*)+"
    r"\.(?:efx|dds|msh|ogg|png)$", re.I)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def rename(name, src, dst):
    """`ch01B0000.msh` -> `ch03B0000.msh`。

    **只改第 4 个字符**，所以 `ch` / `Ch` / `CH` 的大小写原样保留
    （`Ch01@Dash02.mtn` 是 ch01 里唯一一个大写 C 的文件），
    `^C` / `^T` / `^W` 这些部件名也不受影响。
    """
    if name[:4].lower() != src.lower():
        raise ValueError("文件名不是以 %s 开头：%s" % (src, name))
    return name[:2] + dst[2:] + name[4:]


def iter_utf16_runs(blob, min_len=4):
    """扫出 `.msh` 里所有**连续可打印 ASCII 的 UTF-16LE 段**，产出 `(偏移, 文本)`。

    ★ **不要试图去找那个 `u32 长度前缀`**。2026-09-15 实测：
    `ch0300015.msh` 的 `0x48` 处恰好是 `01 00 00 00`，后面两字节 `2b 00` 是可打印的
    `"+"` —— 于是「u32 在 1..200 且后面是合法 UTF-16」这条启发式在 `0x48` 假命中，
    把 `0x4c` 上**真正的**长度前缀连同整条挂接特效路径一起跳过去了。
    找连续段没有这个问题：它不依赖任何「从哪里开始」的猜测。
    """
    n = len(blob)
    i = 0
    while i + 1 < n:
        if 0x20 <= blob[i] < 0x7F and blob[i + 1] == 0:
            j = i
            while j + 1 < n and 0x20 <= blob[j] < 0x7F and blob[j + 1] == 0:
                j += 2
            if (j - i) // 2 >= min_len:
                yield i, blob[i:j].decode("utf-16le")
            i = j
        else:
            i += 1


def patch_msh(blob, tex_map):
    """把 `.msh` 里的贴图名按 `tex_map` 改掉。

    只替换「**长度前缀 + 恰好等于某个已知 .dds 文件名**」的串。
    `ch01` -> `ch03` 等长 ⇒ 长度前缀不用动、文件长度不变、任何偏移都不动。
    含 `/` 或 `\\` 的路径串（挂接特效）天然匹配不上，不会被碰。
    """
    hits = 0
    for old, new in tex_map.items():
        assert len(old) == len(new), (old, new)
        needle = struct.pack("<I", len(old)) + old.encode("utf-16le")
        repl = struct.pack("<I", len(new)) + new.encode("utf-16le")
        if needle in blob:
            hits += blob.count(needle)
            blob = blob.replace(needle, repl)
    return blob, hits


# ---------------------------------------------------------------------------
# 计划
# ---------------------------------------------------------------------------

def build_plan(src, dst):
    """算出「目标文件名 -> 内容字节」的完整集合，外加一份改动报告。"""
    src_dir = os.path.join(CHARS, src)
    if not os.path.isdir(src_dir):
        raise SystemExit("源角色目录不存在：%s" % src_dir)

    names = sorted(os.listdir(src_dir))
    bad = [n for n in names if os.path.splitext(n)[1].lower() not in KNOWN_EXT]
    if bad:
        raise SystemExit("源目录里有不认识的扩展名，先查清楚再跑：%s" % bad[:10])

    # 贴图改名表：源 .dds 文件名 -> 目标 .dds 文件名
    tex_map = {n: rename(n, src, dst)
               for n in names if n.lower().endswith(".dds")}

    plan = {}
    report = {"msh": 0, "msh_patched": 0, "dds": 0, "mtn": 0, "evn": 0,
              "tex_hits": 0}
    for name in names:
        with open(os.path.join(src_dir, name), "rb") as fh:
            blob = fh.read()
        ext = os.path.splitext(name)[1].lower()
        if ext == ".msh":
            blob, hits = patch_msh(blob, tex_map)
            report["msh"] += 1
            report["tex_hits"] += hits
            if hits:
                report["msh_patched"] += 1
        else:
            # .dds 只改文件名；.mtn / .evn 内部一个字节都不改（见模块说明）
            report[ext[1:]] += 1
        plan[rename(name, src, dst)] = blob

    for alias, source in sorted(ALIASES.get((src, dst), {}).items()):
        if source not in plan:
            raise SystemExit("别名的源文件不在计划里：%s <- %s" % (alias, source))
        plan[alias] = plan[source]

    return plan, report


def apply_plan(plan, dst, dry_run=False, prune=True, force=False):
    """把计划落盘。**只写内容不同的文件**（幂等）。

    ★★ **下游改动保护**：目标文件已经存在、内容又和克隆件不一样，说明有人在
    克隆之后**改过它**（X1 就是这样：`tools/ch03_skin/` 把 19 个贴图重着色、
    还把耳朵改尖了）。这时**默认不覆盖**，只把名单报出来 —— 不然「顺手重跑一遍
    克隆脚本」就会把外观改造悄悄抹平，而且因为长度没变，肉眼根本看不出来。
    真要重建就加 `--force`，然后按 recolor.py → ears.py → geometry.py → weapons.py 的顺序重跑
    `tools/ch03_skin/` 下那四个脚本（它们都从只读母本 ch01 出发、幂等；
    geometry.py 必须在 recolor 之后，它会把 0000/B0000 的贴图升成 128×128 并把 UV ×0.5；
    weapons.py 重做 W0001/2/3 + D0002 + D0003A，和前三个互不依赖，放最后只是为了顺序好记）。
    """
    dst_dir = os.path.join(CHARS, dst)
    written = skipped = removed = 0
    clobber = []
    if not dry_run:
        os.makedirs(dst_dir, exist_ok=True)
    existing = set(os.listdir(dst_dir)) if os.path.isdir(dst_dir) else set()

    for name in sorted(plan):
        path = os.path.join(dst_dir, name)
        if name in existing:
            with open(path, "rb") as fh:
                if fh.read() == plan[name]:
                    skipped += 1
                    continue
            if not force:
                clobber.append(name)
                continue
        if not dry_run:
            with open(path, "wb") as fh:
                fh.write(plan[name])
        written += 1

    if prune:
        for name in sorted(existing - set(plan)):
            removed += 1
            if not dry_run:
                os.remove(os.path.join(dst_dir, name))

    return written, skipped, removed, clobber


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------

def _read_ini(path, encodings=("utf-16", "cp949")):
    raw = open(path, "rb").read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for enc in encodings:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin1")


def audit(src, dst):
    """七条体检。任何一条不过就返回非零。"""
    problems = []
    notes = []
    src_dir = os.path.join(CHARS, src)
    dst_dir = os.path.join(CHARS, dst)
    if not os.path.isdir(dst_dir):
        return ["目标目录不存在：%s" % dst_dir], []

    src_names = sorted(os.listdir(src_dir))
    dst_names = sorted(os.listdir(dst_dir))
    dst_set = {n.lower() for n in dst_names}
    n_alias = len(ALIASES.get((src, dst), {}))

    # 1 覆盖 + 扩展名
    #
    # ★ 判据是「**克隆该产出的一个都不少**」，不是「文件数恰好相等」：
    # 外观改造可以往目录里**加**新部件（比如花瓣披风、翅膀件），
    # 那是正常的，不该被这条挡住。少了才是真问题。
    expect = {rename(n, src, dst) for n in src_names}
    expect |= set(ALIASES.get((src, dst), {}))
    missing = sorted(expect - set(dst_names))
    if missing:
        problems.append("① 克隆件缺了 %d 个：%s" % (len(missing), missing[:10]))
    extra = sorted(set(dst_names) - expect)
    if extra:
        notes.append("① 目录里另有 %d 个**新增**文件（外观改造加的部件）：%s"
                     % (len(extra), extra[:12]))
    bad = [n for n in dst_names if os.path.splitext(n)[1].lower() not in KNOWN_EXT]
    if bad:
        problems.append("① 出现不认识的扩展名：%s" % bad[:10])

    # 2/3 .msh 里的串
    external = []
    checked = 0
    for name in dst_names:
        if not name.lower().endswith(".msh"):
            continue
        blob = open(os.path.join(dst_dir, name), "rb").read()
        for _, text in iter_utf16_runs(blob):
            if "/" in text or "\\" in text:
                # 挂接特效路径之类的外部引用 —— 故意不改，只报告。
                # 整段匹配才算，否则二进制里的偶然可打印段会刷屏。
                if _EXT_PATH.match(text):
                    external.append((name, text))
                continue
            for m in _BARE_FILE.finditer(text):
                ref = m.group(0)
                checked += 1
                if src.lower() in ref.lower():
                    problems.append("③ %s 里还残留源角色的文件名：%s" % (name, ref))
                elif ref.lower() not in dst_set:
                    problems.append("② %s 引用的 %s 在目标目录里不存在" % (name, ref))
    notes.append("②③ 查过 %d 处 .msh 内部文件名引用，全部命中目标目录" % checked)
    if external:
        uniq = sorted({t for _, t in external})
        stale = [t for t in uniq if src.lower() in t.lower()]
        notes.append("③ %d 个 .msh 带**外部路径**串（挂接特效，故意保留不改）：%s"
                     % (len(external), uniq))
        if stale:
            notes.append("   其中 %d 条仍指向源角色的特效 —— **这是对的**，"
                         "`Effects/ArmorSpakle/` 下没有 %s 版本，改了就断链"
                         % (len(stale), dst.upper()))

    # 4 .mtn 逐字节相同
    diff = []
    for name in src_names:
        if not name.lower().endswith(".mtn"):
            continue
        tgt = rename(name, src, dst)
        a = open(os.path.join(src_dir, name), "rb").read()
        try:
            b = open(os.path.join(dst_dir, tgt), "rb").read()
        except OSError:
            diff.append(tgt)
            continue
        if a != b:
            diff.append(tgt)
    if diff:
        problems.append("④ 这些 .mtn 和源不是逐字节相同：%s" % diff[:10])

    # 5 .evn 死引用不能变多
    def dead_refs(d, names):
        dead = set()
        for name in names:
            if not name.lower().endswith(".evn"):
                continue
            try:
                text = open(os.path.join(d, name), "rb").read().decode("cp949", "replace")
            except OSError:
                continue
            for ref in re.findall(r"<ResourcePath>([^<]+)</ResourcePath>", text):
                for base in (os.path.join(PACK, "Sounds"),
                             os.path.join(PACK, "Effects"),
                             PACK):
                    if os.path.exists(os.path.join(base, ref.replace("/", os.sep))):
                        break
                else:
                    dead.add(ref)
        return dead

    src_dead = dead_refs(src_dir, src_names)
    dst_dead = dead_refs(dst_dir, dst_names)
    extra = dst_dead - src_dead
    if extra:
        problems.append("⑤ 克隆出了**新的**死引用：%s" % sorted(extra)[:10])
    notes.append("⑤ 死引用：源 %d 条 / 目标 %d 条（原版自带，不是本次引入）"
                 % (len(src_dead), len(dst_dead)))

    # 6 反向引用：weapon.ini 点名的 ch03 资源在不在
    #
    # ★ 必须**逐行**解析，不能拿一条正则在全文里扫：weapon.ini 里有
    #   `Sound-Load=`（空值）这种行，`\s*` 会把换行一起吃掉，
    #   于是把下一整行当成了值。2026-09-15 实测踩过。
    miss_model, miss_sound, miss_efx = [], [], []
    wp = _read_ini(os.path.join(DATA, "weapon.ini"))
    tag = "/" + dst.lower() + "/"
    for line in wp.splitlines():
        line = line.strip()
        if not line or line[0] in "#;[" or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip().lstrip("_"), val.strip()
        if not val:
            continue
        low = val.lower().replace("\\", "/")
        if tag not in low:
            continue
        if key == "Image" and low.startswith("model,"):
            rel = val.split(",", 1)[1].strip()
            if not os.path.exists(os.path.join(PACK, "Models",
                                               rel.replace("/", os.sep) + ".msh")):
                miss_model.append(rel + ".msh")
        elif low.startswith("fx/"):
            if not os.path.exists(os.path.join(PACK, "Sounds",
                                               val.replace("/", os.sep))):
                miss_sound.append(val)
        elif low.startswith("effects/"):
            if not os.path.exists(os.path.join(PACK, val.replace("/", os.sep))):
                miss_efx.append(val)
    if miss_model:
        problems.append("⑥ weapon.ini 点名的模型缺文件：%s" % miss_model)
    if miss_efx:
        problems.append("⑥ weapon.ini 点名的特效缺文件：%s" % miss_efx)
    if miss_sound:
        uniq_sound = sorted(set(miss_sound))
        notes.append("⑥ weapon.ini 点名的音效缺 %d 个（**预期如此**，见 FINDINGS §4）：\n"
                     "        %s" % (len(uniq_sound), "\n        ".join(uniq_sound)))
    else:
        notes.append("⑥ weapon.ini 点名的 %s 资源：模型 / 特效 / 音效全部命中" % dst)

    # 7 图集守卫
    try:
        blob = open(ICON_SMF, "rb").read()
        frames = struct.unpack_from("<I", blob, 4)[0]
        if frames < ICON_MIN_FRAMES:
            problems.append("⑦ %s 只有 %d 帧（要 >= %d）—— M2 还没做，"
                            "现在开闸会**崩在大厅**（FINDINGS §3）"
                            % (os.path.basename(ICON_SMF), frames, ICON_MIN_FRAMES))
        else:
            notes.append("⑦ 大头像图集 %d 帧，够用" % frames)
    except OSError as exc:
        problems.append("⑦ 读不到 %s：%s" % (ICON_SMF, exc))

    return problems, notes


# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="把一个角色的整套模型克隆成另一个角色")
    ap.add_argument("--from", dest="src", default="ch01", help="源角色目录名，默认 ch01")
    ap.add_argument("--to", dest="dst", default="ch03", help="目标角色目录名，默认 ch03")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    ap.add_argument("--audit", nargs="?", const="", metavar="chNN",
                    help="只跑体检，不写文件")
    ap.add_argument("--no-prune", action="store_true",
                    help="不删除目标目录里本次没产出的文件")
    ap.add_argument("--force", action="store_true",
                    help="连克隆之后被改过的文件一起覆盖（会抹掉外观改造，慎用）")
    args = ap.parse_args(argv)

    if args.audit is not None:
        dst = args.audit or args.dst
        problems, notes = audit(args.src, dst)
        for line in notes:
            print("   " + line)
        if problems:
            print("\n体检不过 —— %d 条：" % len(problems))
            for line in problems:
                print("  ✗ " + line)
            return 1
        print("\n体检全过 ✓")
        return 0

    plan, report = build_plan(args.src, args.dst)
    print("%s -> %s：%d 个目标文件"
          "（msh %d / dds %d / mtn %d / evn %d，别名 %d）"
          % (args.src, args.dst, len(plan), report["msh"], report["dds"],
             report["mtn"], report["evn"], len(ALIASES.get((args.src, args.dst), {}))))
    print("   .msh 里改掉的贴图名：%d 处，分布在 %d 个文件"
          % (report["tex_hits"], report["msh_patched"]))
    for alias, source in sorted(ALIASES.get((args.src, args.dst), {}).items()):
        print("   别名：%s  <-  %s（字节原样）" % (alias, source))

    written, skipped, removed, clobber = apply_plan(
        plan, args.dst, dry_run=args.dry_run, prune=not args.no_prune,
        force=args.force)
    print("%s写入 %d / 未变 %d / 删除 %d / **保住的下游改动 %d**"
          % ("[dry-run] " if args.dry_run else "",
             written, skipped, removed, len(clobber)))

    if clobber:
        print()
        print("   ★ 下面这 %d 个文件在克隆之后被改过，**没有覆盖**：" % len(clobber))
        for name in clobber:
            print("       " + name)
        print("   这多半是 tools/ch03_skin/ 的外观改造（重着色 + 尖耳 + 追加几何）。")
        print("   真要从母本重建：加 --force，然后**按这个顺序**跑")
        print('       "C:\\Python314\\python.exe" tools/ch03_skin/recolor.py')
        print('       "C:\\Python314\\python.exe" tools/ch03_skin/ears.py')
        print('       "C:\\Python314\\python.exe" tools/ch03_skin/geometry.py   # 它把 0000/B0000 的贴图升到 128x128、UV x0.5，必须在 recolor 之后')
        print('       "C:\\Python314\\python.exe" tools/ch03_skin/weapons.py    # 三把武器 + 两个弹体（W0001/2/3、D0002、D0003A），放最后')

    if args.dry_run:
        return 0

    problems, notes = audit(args.src, args.dst)
    print()
    for line in notes:
        print("   " + line)
    if problems:
        print("\n体检不过 —— %d 条：" % len(problems))
        for line in problems:
            print("  ✗ " + line)
        return 1
    print("\n体检全过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
