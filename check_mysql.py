import os
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST'),
    port=int(os.getenv('MYSQL_PORT', '3306')),
    user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASSWORD'),
    database=os.getenv('MYSQL_DATABASE')
)
cur = conn.cursor()
cur.execute("SHOW TABLES")
all_tables = [table[0] for table in cur.fetchall()]
print(all_tables)
print("sync_runs present:", "sync_runs" in all_tables)
print("vw_winordetraba_mirror present:", "vw_winordetraba_mirror" in all_tables)
if "sync_runs" in all_tables:
    cur.execute("SELECT COUNT(*) FROM sync_runs")
    print("sync_runs rows:", cur.fetchone()[0])
conn.close()
