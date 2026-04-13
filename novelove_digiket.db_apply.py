import sqlite3
conn = sqlite3.connect('/home/kusanagi/scripts/novelove_digiket.db')
c = conn.cursor()
c.executemany("UPDATE novelove_posts SET desc_score = ? WHERE product_id = ? AND status = 'published'", [(4, 'ITM0332641'), (4, 'ITM0333637'), (4, 'ITM0333635'), (4, 'ITM0333633'), (4, 'ITM0333632'), (4, 'ITM0333631'), (4, 'ITM0333630'), (4, 'ITM0333628'), (4, 'ITM0333627'), (4, 'ITM0333626'), (4, 'ITM0333722'), (4, 'ITM0333721'), (4, 'ITM0333720'), (4, 'ITM0333719'), (4, 'ITM0333718'), (4, 'ITM0333717'), (4, 'ITM0333715'), (4, 'ITM0333714'), (4, 'ITM0333858'), (4, 'ITM0333856'), (4, 'ITM0333855'), (4, 'ITM0333854'), (4, 'ITM0333852'), (4, 'ITM0333941'), (4, 'ITM0333940'), (4, 'ITM0333939'), (4, 'ITM0334068'), (4, 'ITM0334164'), (4, 'ITM0334168'), (4, 'ITM0334508'), (4, 'ITM0334520')])
conn.commit()
conn.close()
print("Success")
