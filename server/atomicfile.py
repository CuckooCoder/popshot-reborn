#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atomicfile.py —— 「写 tmp → fsync → 改名顶上去」里的**最后那一句改名**。

## 为什么值得一个单独的文件（2026-09-14）

`os.replace()` 在 Windows 上会**随机**失败：

    PermissionError: [WinError 5] 拒绝访问。

Windows 的重命名要求源和目标都没有别人用「不许删」的方式开着，而在一台
正常的 Windows 上总有别人会去开刚落盘的文件 —— 杀软（实时防护一落盘就扫）、
Windows Search 索引、OneDrive / 坚果云一类的同步盘、备份软件。它们开的时间
只有毫秒级，但撞上了就是当场抛异常，**这一次写盘就没了**。

实测（本机，纯 `os.replace`，一行业务代码都不碰）：

    1  个进程 × 6400 次替换 ……  0 次失败
    16 个进程 ×  400 次替换 ……  9 次失败（0.14%）

⇒ 它只和「机器上装了什么、同时有多少人在写盘」有关，和代码无关。云主机、
玩家自己的电脑上一样会犯 —— **不能指望每个人去改杀软设置**，只能让写盘
这条路自己扛住。`account_store.py` 文件头那句「结算奖励就丢了」说的就是它。

## 两种「被占着」必须分开

* **一瞬的**（杀软扫一眼、索引器读一眼）：毫秒级就放手，再试一次就过去了；
* **一直的**（有人拿编辑器独占打开了 `shop.json`）：等到天荒地老也不会放手。

所以这里的做法是「在 `PATIENCE` 秒内反复再试，到点仍不行就把**原来那个**
`PermissionError` 原样抛出去」。第二种照旧会走到 `web/admin.py` 那句
「这个文件多半正被别的程序占着（编辑器打开了它？），关掉再存一次」，
一个字都没变 —— 重试只吃掉第一种，不掩盖第二种。

## 铁律 10 的豁免

`PATIENCE` 是个固定时间阈值，而铁律 10 禁止这种东西。豁免理由就是铁律
自己写明的那一条：**物理上没有事件可等**。Windows 不会在别人的句柄关掉时
通知任何人（`ReadDirectoryChangesW` 也不报「句柄关了」），唯一能知道的
办法就是再 `os.replace` 一次看看。

而且这个数不是拿某台机器的观测值调出来的，它只回答一个二选一：比
「杀软扫一个小文件」明显长、比「人愿意干等」明显短。两头差着三个数量级，
1 秒落在中间，换台快机器还是慢机器都不影响结论。

## 两个函数

* `replace(src, dst)` —— 覆盖式，给「tmp 写完顶掉旧文件」用（10 处）；
* `rename(src, dst)` —— 非覆盖式，给「搬**目录**」用（4 处）。
  Windows 上 `os.replace` 覆盖不了一个已经存在的目录，而 `crashstore` /
  `databackup` 的落位正是靠「目标必须不存在」保证不撞名的，语义不能换。

只用标准库；发布运行时是 CPython 3.8。
"""
import os
import time

#: 撞上「被别人占着」之后最多再试这么久（秒）。见文件头「铁律 10 的豁免」。
PATIENCE = 1.0

#: 两次重试之间等多久（秒）：从 `_BACKOFF_MIN` 起步、每次翻倍、封顶
#: `_BACKOFF_MAX`。起步值的含义是「比一次系统调用长一点」，不是观测值。
_BACKOFF_MIN = 0.001
_BACKOFF_MAX = 0.05

#: 一共重试过几次。**只给日志和单测看，不参与任何判断**；多线程下数漏一两次
#: 也无所谓，所以不上锁。
retried = 0


def _retrying(op, src, dst, patience, _sleep, _clock):
    """`op(src, dst)`，撞上「被别人占着」就在 `patience` 秒内反复再试。"""
    if os.name != "nt":
        # ★ 只有 Windows 有这个现象：POSIX 的 `rename()` 根本不看别人开没开。
        #   那边的 `PermissionError` 是真的权限不对，重试只会白等一秒。
        op(src, dst)
        return
    global retried
    deadline = _clock() + patience
    wait = _BACKOFF_MIN
    while True:
        try:
            op(src, dst)
            return
        except PermissionError:
            if _clock() >= deadline:
                raise
            retried += 1
            _sleep(wait)
            wait = min(wait * 2, _BACKOFF_MAX)


def replace(src, dst, patience=PATIENCE, _sleep=time.sleep,
            _clock=time.monotonic):
    """`os.replace(src, dst)`，撞上「文件被别人占着」就在 `patience` 秒内重试。

    `_sleep` / `_clock` 是给单测的注入点（和 `crashwatch` 同一套做法），
    业务代码别传。
    """
    _retrying(os.replace, src, dst, patience, _sleep, _clock)


def rename(src, dst, patience=PATIENCE, _sleep=time.sleep,
           _clock=time.monotonic):
    """`os.rename(src, dst)` 的同一套重试。**搬目录**用这个。

    ★ 为什么不一律用 `replace()`：Windows 上 `os.replace` 覆盖不了一个
      已经存在的**目录**，而这几处（`crashstore` / `databackup` 的落位和
      「先改名再 rmtree」）搬的正是目录、且靠「目标必须不存在」保证不撞名。
      语义得留着，只给它加重试。

    ★ 目录版的失败没能在本机压测里复现（16 进程 × 200 次 = 0 次），
      盖上是因为**失败的后果一样是「悄悄少了一份」**：`crashstore` 那一处
      少一份崩溃现场，`databackup` 那一处少一份备份，两边都只会在日志里
      留一行，没人会发现。
    """
    _retrying(os.rename, src, dst, patience, _sleep, _clock)
