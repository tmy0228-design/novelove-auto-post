import sqlite3
conn=sqlite3.connect('novelove.db')
cur=conn.cursor()
cur.execute("SELECT status, COUNT(*) FROM novelove_posts WHERE site LIKE '%Lovecal%' GROUP BY status;")
print('Lovecal normal:', cur.fetchall())
