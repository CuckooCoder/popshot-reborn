/* --------------------------------------------------------------------------
   log.h —— logs\updater.log 追加日志（UTF-8）。所有关键节点都落一行，
   排查「更新到一半没反应」的现场问题用。

   ★ 跨天会切成 logs\updater-YYYYMMDD.log（用户 2026-09-14）。
     不切的话它就是个只追加、永不结束的文件 —— 而 logs\ 的自动清理
     （server\logcleanup.py）判据是 mtime，**一直在写的文件永远是「刚才」**，
     保留天数对它一天都不起作用。服务端那边同一个毛病、同一个修法，
     见 server\daylog.py 的文件头。
   -------------------------------------------------------------------------- */
#ifndef UPDATER_LOG_H
#define UPDATER_LOG_H

#include <stdarg.h>

/* 初始化（记下包根；同时把自身身份写一行）。 */
void log_init(const wchar_t *package_root, const char *tag_line);
/* 追加一行（UTF-8 写出，时间戳自动加）。中文请传宽串用 %ls。 */
void log_line(const char *fmt, ...);
/* 同上（显式 va_list 版）。 */
void log_vline(const char *fmt, va_list ap);

/* `…\updater.log` + 2026/9/13 -> `…\updater-20260913.log`。
   切名和自检共用同一份规则；out 放不下就返回 0、不碰 out 的内容以外的东西。
   ★ 命名必须和 server\daylog.py 的 dated_name() 一致 —— 运维看 logs\ 时
     不该看到两种切法。 */
int log_dated_name(const wchar_t *path, unsigned year, unsigned month,
                   unsigned day, wchar_t *out, size_t cap);

#endif /* UPDATER_LOG_H */
