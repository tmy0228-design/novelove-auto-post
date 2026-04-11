#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
バックアップ vs 現在DB の突き合わせ検証 → OK なら全バックアップ削除
"""
import paramiko
import sys

HOST = "novelove.jp"
USER = "root"
PASS = "#Dama0228"

REMOTE_SCRIPT = r"""#!/usr/bin/env python3
import sqlite3, glob, os

DB = "/home/kusanagi/scripts/novelove_digiket.db"
M = chr(0xFFFD)
like_q = "%" + M + "%"

# 最新のバックアップを特定
backups = sorted(glob.glob(DB + ".backup_*"))
if not backups:
    print("ERROR: no backup found")
    exit(1)
latest_bk = backups[-1]
print("BACKUP_FILE: %s" % latest_bk)

# バックアップDBを読む
bk_conn = sqlite3.connect(latest_bk)
bk_cur = bk_conn.cursor()

bk_total = bk_cur.execute("SELECT count(*) FROM novelove_posts").fetchone()[0]
bk_published = bk_cur.execute("SELECT count(*) FROM novelove_posts WHERE status='published'").fetchone()[0]
bk_broken = bk_cur.execute("SELECT count(*) FROM novelove_posts WHERE (title LIKE ? OR description LIKE ?) AND status != 'published'", (like_q, like_q)).fetchone()[0]
bk_pub_ids = set(r[0] for r in bk_cur.execute("SELECT product_id FROM novelove_posts WHERE status='published'").fetchall())
bk_conn.close()

# 現在のDBを読む
cur_conn = sqlite3.connect(DB)
cur_cur = cur_conn.cursor()

cur_total = cur_cur.execute("SELECT count(*) FROM novelove_posts").fetchone()[0]
cur_published = cur_cur.execute("SELECT count(*) FROM novelove_posts WHERE status='published'").fetchone()[0]
cur_mojibake = cur_cur.execute("SELECT count(*) FROM novelove_posts WHERE (title LIKE ? OR description LIKE ?) AND status != 'published'", (like_q, like_q)).fetchone()[0]
cur_pub_ids = set(r[0] for r in cur_cur.execute("SELECT product_id FROM novelove_posts WHERE status='published'").fetchall())
cur_conn.close()

# 検証
print("")
print("=== BACKUP ===")
print("  total: %d" % bk_total)
print("  published: %d" % bk_published)
print("  mojibake(non-pub): %d" % bk_broken)

print("")
print("=== CURRENT ===")
print("  total: %d" % cur_total)
print("  published: %d" % cur_published)
print("  mojibake(non-pub): %d" % cur_mojibake)

print("")
print("=== CHECKS ===")

# Check 1: published件数が一致
c1 = bk_published == cur_published
print("  published_count_match: %s (%d == %d)" % (c1, bk_published, cur_published))

# Check 2: published の product_id が全て残っている
missing = bk_pub_ids - cur_pub_ids
c2 = len(missing) == 0
print("  published_ids_intact: %s (missing: %d)" % (c2, len(missing)))

# Check 3: 文字化けレコードがゼロ
c3 = cur_mojibake == 0
print("  mojibake_cleared: %s (remaining: %d)" % (c3, cur_mojibake))

# Check 4: 減った件数 = バックアップの文字化け件数
diff = bk_total - cur_total
c4 = diff == bk_broken
print("  deleted_count_match: %s (deleted %d == broken %d)" % (c4, diff, bk_broken))

all_ok = c1 and c2 and c3 and c4
print("")
print("ALL_OK: %s" % all_ok)

if all_ok and "--cleanup" in __import__("sys").argv:
    for bk in backups:
        os.remove(bk)
        print("REMOVED: %s" % bk)
    print("CLEANUP_DONE")
elif all_ok:
    print("VERIFY_ONLY: pass --cleanup to delete backups")
else:
    print("ABORT: checks failed, keeping backups")
"""

REMOTE_PATH = "/tmp/_verify_mojibake.py"

cleanup = "--cleanup" in sys.argv

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PASS, timeout=30, look_for_keys=False, allow_agent=False)

sftp = ssh.open_sftp()
with sftp.open(REMOTE_PATH, "w") as f:
    f.write(REMOTE_SCRIPT)
sftp.close()

cmd = "python3 %s" % REMOTE_PATH
if cleanup:
    cmd += " --cleanup"

sin, sout, serr = ssh.exec_command(cmd)
out = sout.read().decode()
err = serr.read().decode()

print(out)
if err:
    print("STDERR:", err)

ssh.exec_command("rm -f %s" % REMOTE_PATH)
ssh.close()
