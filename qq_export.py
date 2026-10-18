#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQ NT 聊天记录导出器 —— 解密后导出为纯文本 txt。

用法:
    python qq_export.py --db decrypted.db --out ./out [--me 你的QQ号]

前置: 需要先解密 nt_msg.db（见 decrypt_qq.py / README）。
本工具只读取本地已解密数据库，导出成和微信导出一致的 txt 格式:

    2024-01-01 12:00:00 '发送者'
    消息内容

注意: 库里常有坏页，普通全表扫描撞到坏页就会报 malformed 并中断。
这里按 rowid 分批读表本身的 B 树（不走索引），撞到坏页时只跳过坏掉的
那一小段 rowid 区间，其余部分照常读完；排序在 Python 里做。
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "vendor_chatlog_keeper"))
try:
    from chatlog_keeper.qq_db import _extract_msg_text
except Exception:
    # 允许把 chatlog_keeper 装在环境里
    from chatlog_keeper.qq_db import _extract_msg_text  # noqa

# NTQQ 数值列名
COL_TIME = "40050"     # unix 秒
COL_SUIN = "40033"     # 发送者 QQ 号
COL_BODY = "40800"     # protobuf blob 正文
COL_PEER = "40030"     # c2c 对端 QQ 号（40002 是消息序号，不是 QQ 号）
COL_GCODE = "40021"    # 群号
COL_SNAME = "40090"    # 发送者昵称


ROWID_MAX = (1 << 63) - 1


def _read_after(con, table, cols, after, n):
    return con.execute(f"SELECT rowid,{cols} FROM {table} WHERE rowid > ? ORDER BY rowid LIMIT ?",
                       (after, n)).fetchall()


def _readable(con, table, pos):
    try:
        _read_after(con, table, "1", pos, 1)
        return True
    except sqlite3.DatabaseError:
        return False


def _skip_bad(con, table, after):
    """after 之后紧跟坏页：先指数试探、再二分，返回最近一个可读的起点（找不到返回 None）。"""
    gap = 1
    while after + gap < ROWID_MAX and not _readable(con, table, after + gap):
        gap *= 2
    if after + gap >= ROWID_MAX:
        return None
    lo, hi = gap // 2, gap
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if _readable(con, table, after + mid):
            hi = mid
        else:
            lo = mid
    return after + hi


def scan_table(con, table, batch=5000, skipped=None):
    """按 rowid 分批流式扫描一个消息表，撞到坏页只跳过坏区间；逐行 yield。

    skipped: 可选 list，记录被跳过的 (起, 止) rowid 区间。
    """
    cols = f'"{COL_TIME}","{COL_SUIN}","{COL_BODY}","{COL_PEER}","{COL_GCODE}","{COL_SNAME}"'
    after = -ROWID_MAX - 1
    n = batch
    while True:
        try:
            rows = _read_after(con, table, cols, after, n)
        except sqlite3.DatabaseError:
            if n > 1:
                # 批里某处有坏页 —— 缩小批量逼近它
                n //= 2
                continue
            nxt = _skip_bad(con, table, after)
            if skipped is not None:
                skipped.append((after, nxt if nxt is not None else ROWID_MAX))
            if nxt is None:
                return
            after, n = nxt, batch
            continue
        if not rows:
            return
        for r in rows:
            yield r[1:]
        after = rows[-1][0]
        n = min(batch, n * 2)


def as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def export(db_path, out_dir, me=None, groups="me", only_c2c=None, only_group=None):
    """only_c2c / only_group: 只导出这些好友 QQ 号 / 群号（集合）。
    两者都为 None 时导出全部；只给了其中一个时，另一类会话不导出。"""
    os.makedirs(out_dir, exist_ok=True)
    selective = only_c2c is not None or only_group is not None
    con = sqlite3.connect(db_path)
    con.text_factory = bytes
    me_i = int(me) if me else None

    tables = [r[0].decode() if isinstance(r[0], bytes) else r[0]
              for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]

    # chat_key -> list[(ts, sender_str, text)]
    chats = {}
    stats = {"c2c": 0, "group": 0, "me": 0}

    plans = []
    if "c2c_msg_table" in tables and (not selective or only_c2c):
        plans.append(("c2c_msg_table", False))
    if "group_msg_table" in tables and (not selective or only_group):
        plans.append(("group_msg_table", True))

    for table, is_group in plans:
        kind = "group" if is_group else "c2c"
        wanted = only_group if is_group else only_c2c
        skipped = []
        for t, suin, body, peer, gcode, sname in scan_table(con, table, skipped=skipped):
            # 指定了会话时，先按会话号过滤，跳过其余会话的正文解析
            if wanted and as_int(gcode if is_group else peer) not in wanted:
                continue
            ts = as_int(t)
            if ts < 10 ** 8:
                continue
            suin_i = as_int(suin)
            # 群聊默认只解析"我"发的（避免解析上千万条别人的消息）
            if is_group and groups == "me" and me_i and suin_i != me_i:
                continue
            text = ""
            if body:
                try:
                    text = _extract_msg_text(body)
                except Exception:
                    text = ""
            if not text:
                continue
            chat_id = as_int(gcode) if is_group else as_int(peer)
            prefix = "群聊" if is_group else "私聊"
            chat_key = f"{prefix}_{chat_id}"
            is_me = me and suin_i == int(me)
            sender = "我" if is_me else (
                sname.decode("utf-8", "ignore") if isinstance(sname, bytes) and sname else str(suin_i))
            chats.setdefault(chat_key, []).append((ts, sender, text))
            stats[kind] += 1
            if is_me:
                stats["me"] += 1
        if skipped:
            print(f"{table}: 跳过 {len(skipped)} 段坏页 rowid 区间，其余已读完")
            for a, b in skipped:
                print(f"    ({a}, {b}]")
    con.close()

    # 写每个会话的 txt（按时间排序）+ 汇总"我"的消息
    me_all = []
    for chat_key, rows in chats.items():
        rows.sort(key=lambda x: x[0])
        fp = os.path.join(out_dir, chat_key + ".txt")
        with open(fp, "w", encoding="utf-8") as f:
            for ts, sender, text in rows:
                tstr = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{tstr} '{sender}'\n{text}\n\n")
        if me:
            for ts, sender, text in rows:
                if sender == "我":
                    me_all.append((ts, text))

    if me:
        me_all.sort(key=lambda x: x[0])
        with open(os.path.join(out_dir, "_我发的所有消息.txt"), "w", encoding="utf-8") as f:
            for ts, text in me_all:
                tstr = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"{tstr}\t{text}\n")

    print(f"会话数: {len(chats)}  私聊消息: {stats['c2c']}  群聊消息: {stats['group']}  你(我)的消息: {stats['me']}")
    print(f"输出目录: {out_dir}")


def main():
    ap = argparse.ArgumentParser(description="QQ NT 已解密库 -> txt 导出")
    ap.add_argument("--db", required=True, help="已解密的 SQLite 数据库路径")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--me", help="你的 QQ 号（把你自己标记为'我'并单独汇总）")
    ap.add_argument("--groups", choices=["me", "all"], default="me",
                    help="群聊导出范围: me=只导你自己发的(快, 默认); all=全部(慢, 需解析千万条)")
    ap.add_argument("--c2c", help="只导出这些好友的私聊，逗号分隔的 QQ 号")
    ap.add_argument("--group", help="只导出这些群，逗号分隔的群号")
    args = ap.parse_args()

    def id_set(s):
        return {int(x) for x in s.split(",") if x.strip()} if s else None

    export(args.db, args.out, args.me, args.groups, id_set(args.c2c), id_set(args.group))


if __name__ == "__main__":
    main()
