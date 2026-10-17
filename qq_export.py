#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQ NT 聊天记录导出器 —— 解密后导出为纯文本 txt。

用法:
    python qq_export.py --db decrypted.db --out ./out [--me 你的QQ号]

前置: 需要先解密 nt_msg.db（见 decrypt_qq.py / README）。
本工具只读取本地已解密数据库，导出成和微信导出一致的 txt 格式:

    2024-01-01 12:00:00 '发送者'
    消息内容

注意: QQ 会给消息表建索引，损坏的库用 ORDER BY 会报 malformed，
因此这里一律"不排序扫描 + Python 内排序"，天然容忍坏索引。
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
COL_PEER = "40002"     # c2c 对端 QQ 号
COL_GCODE = "40021"    # 群号
COL_SNAME = "40090"    # 发送者昵称


def scan_table(con, table, batch=5000):
    """不排序流式扫描一个消息表，避开损坏索引；逐批 yield 行。"""
    cur = con.cursor()
    cur.arraysize = batch
    cols = f'"{COL_TIME}","{COL_SUIN}","{COL_BODY}","{COL_PEER}","{COL_GCODE}","{COL_SNAME}"'
    cur.execute(f"SELECT {cols} FROM {table}")
    while True:
        try:
            rows = cur.fetchmany(batch)
        except sqlite3.DatabaseError:
            # 撞到坏页 —— 跳过这一批，继续
            break
        if not rows:
            break
        for r in rows:
            yield r


def as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def export(db_path, out_dir, me=None, groups="me"):
    os.makedirs(out_dir, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.text_factory = bytes
    me_i = int(me) if me else None

    tables = [r[0].decode() if isinstance(r[0], bytes) else r[0]
              for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]

    # chat_key -> list[(ts, sender_str, text)]
    chats = {}
    stats = {"c2c": 0, "group": 0, "me": 0}

    plans = []
    if "c2c_msg_table" in tables:
        plans.append(("c2c_msg_table", False))
    if "group_msg_table" in tables:
        plans.append(("group_msg_table", True))

    for table, is_group in plans:
        kind = "group" if is_group else "c2c"
        for t, suin, body, peer, gcode, sname in scan_table(con, table):
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
    args = ap.parse_args()
    export(args.db, args.out, args.me, args.groups)


if __name__ == "__main__":
    main()
