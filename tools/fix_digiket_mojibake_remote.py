#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
サーバー側 DigiKet DB 文字化けクリーンアップ (SFTPアップロード方式)
--execute: 削除実行
"""
import paramiko
import sys
import tempfile
import os

HOST = "novelove.jp"
USER = "root"
PASS = "#Dama0228"

execute_mode = "--execute" in sys.argv

# サーバー上で実行するスクリプト本体
REMOTE_SCRIPT = r"""#!/usr/bin/env python3
import sqlite3, shutil, os, sys
from datetime import datetime

DB = "/home/kusanagi/scripts/novelove_digiket.db"
M = chr(0xFFFD)
like_q = "%" + M + "%"
execute = "--execute" in sys.argv

# Backup
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
backup = DB + ".backup_" + ts
shutil.copy2(DB, backup)
print("BACKUP: %s (%d bytes)" % (backup, os.path.getsize(backup)))

conn = sqlite3.connect(DB)
cur = conn.cursor()

total = cur.execute("SELECT count(*) FROM novelove_posts").fetchone()[0]
print("TOTAL: %d" % total)

print("STATUS_BREAKDOWN:")
for row in cur.execute("SELECT status, count(*) FROM novelove_posts GROUP BY status ORDER BY count(*) DESC"):
    print("  %s: %d" % (row[0], row[1]))

broken = 0
print("MOJIBAKE_BREAKDOWN:")
for row in cur.execute("SELECT status, count(*) FROM novelove_posts WHERE title LIKE ? OR description LIKE ? GROUP BY status", (like_q, like_q)):
    print("  %s: %d" % (row[0], row[1]))
    broken += row[1]
print("  BROKEN_TOTAL: %d" % broken)

delete_target = cur.execute(
    "SELECT count(*) FROM novelove_posts WHERE (title LIKE ? OR description LIKE ?) AND status != 'published'",
    (like_q, like_q)
).fetchone()[0]
rescrape_target = cur.execute(
    "SELECT count(*) FROM novelove_posts WHERE (title LIKE ? OR description LIKE ?) AND status = 'published'",
    (like_q, like_q)
).fetchone()[0]
clean = total - broken

print("DELETE_TARGET: %d" % delete_target)
print("RESCRAPE_TARGET: %d" % rescrape_target)
print("CLEAN: %d" % clean)

sanity = (delete_target + rescrape_target + clean == total)
print("SANITY: %s" % sanity)

if not sanity:
    print("ABORT: sanity check failed")
    conn.close()
    sys.exit(1)

if not execute:
    print("DRY_RUN: no changes made")
    conn.close()
    sys.exit(0)

# Delete
cur.execute(
    "DELETE FROM novelove_posts WHERE (title LIKE ? OR description LIKE ?) AND status != 'published'",
    (like_q, like_q)
)
deleted = cur.rowcount
conn.commit()

post_count = cur.execute("SELECT count(*) FROM novelove_posts").fetchone()[0]
post_pub = cur.execute("SELECT count(*) FROM novelove_posts WHERE status = 'published'").fetchone()[0]

print("DELETED: %d" % deleted)
print("POST_TOTAL: %d" % post_count)
print("POST_PUBLISHED: %d" % post_pub)
print("FINAL_OK: %s" % (post_count == total - delete_target))
conn.close()
"""

REMOTE_PATH = "/tmp/_fix_mojibake.py"

# SSH接続
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASS, timeout=30, look_for_keys=False, allow_agent=False)

# SFTPでスクリプトをアップロード
sftp = ssh.open_sftp()
with sftp.open(REMOTE_PATH, "w") as f:
    f.write(REMOTE_SCRIPT)
sftp.close()

# 実行
cmd = f"python3 {REMOTE_PATH}"
if execute_mode:
    cmd += " --execute"
sin, sout, serr = ssh.exec_command(cmd)
out = sout.read().decode()
err = serr.read().decode()

print(out)
if err:
    print("STDERR:", err)

# 後片付け
ssh.exec_command(f"rm -f {REMOTE_PATH}")
ssh.close()
