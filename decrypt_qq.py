#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQ NT nt_msg.db 解密器 —— 输出为普通 SQLite 数据库。

用法:
    python decrypt_qq.py --db nt_msg.db --key-file qq_db.key --out decrypted.db
    python decrypt_qq.py --db nt_msg.db --key "<16位口令>" --out decrypted.db

参数（QQ 9.9.x 已确认）: SQLCipher, AES-256-CBC, PBKDF2-HMAC-SHA512 4000 轮,
page_size=4096, reserve=48, 文件头部有 1024 字节 QQ 自定义头需剥掉。
密钥获取见 README（chatlog-keeper extract-key）。
"""
import argparse
import hashlib
import os
import sqlite3

from Crypto.Cipher import AES

PAGE_SZ, RESERVE, KEY_SZ, HEADER = 4096, 48, 32, 1024


def decrypt(db_path, key, out_path):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(db_path, "rb") as f:
        f.seek(HEADER)  # 剥掉 QQ 自定义头
        first = f.read(PAGE_SZ)
        salt = first[:16]
        enc_key = hashlib.pbkdf2_hmac("sha512", key, salt, 4000, dklen=KEY_SZ)
        with open(out_path, "wb") as out:
            iv = first[PAGE_SZ - RESERVE:PAGE_SZ - RESERVE + 16]
            dec = AES.new(enc_key, AES.MODE_CBC, iv).decrypt(first[16:PAGE_SZ - RESERVE])
            out.write(b"SQLite format 3\x00" + dec + first[PAGE_SZ - RESERVE:])
            n = 1
            while True:
                page = f.read(PAGE_SZ)
                if len(page) < PAGE_SZ:
                    if page:
                        out.write(page)
                    break
                iv = page[PAGE_SZ - RESERVE:PAGE_SZ - RESERVE + 16]
                dec = AES.new(enc_key, AES.MODE_CBC, iv).decrypt(page[:PAGE_SZ - RESERVE])
                out.write(dec + page[PAGE_SZ - RESERVE:])
                n += 1
    print("decrypted pages:", n, "-> size GB %.2f" % (os.path.getsize(out_path) / 1024 ** 3))


def check(out_path):
    con = sqlite3.connect(out_path)
    try:
        tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        print("TABLES (%d):" % len(tabs), tabs[:40])
        for t in tabs:
            if t in ("c2c_msg_table", "group_msg_table"):
                try:
                    c = con.execute('SELECT count(*) FROM "%s"' % t).fetchone()[0]
                    print("   %-30s rows=%s" % (t, c))
                except Exception as e:
                    print("   %-30s ERR %s" % (t, e))
    except Exception as e:
        print("SCHEMA READ FAILED:", e, "（密钥不对，或 QQ 未正常退出导致坏页）")
    con.close()


def main():
    ap = argparse.ArgumentParser(description="QQ NT nt_msg.db -> 明文 SQLite")
    ap.add_argument("--db", required=True, help="加密的 nt_msg.db 路径")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--key-file", help="存放 16 位口令的文件（chatlog-keeper 的 qq_db.key）")
    g.add_argument("--key", help="直接给出 16 位口令")
    ap.add_argument("--out", required=True, help="输出的明文数据库路径")
    args = ap.parse_args()

    if args.key_file:
        with open(args.key_file, "rb") as kf:
            key = kf.read().strip()
    else:
        key = args.key.encode()
    decrypt(args.db, key, args.out)
    check(args.out)


if __name__ == "__main__":
    main()
