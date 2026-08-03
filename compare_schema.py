import os
import re
import pyodbc
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

azure_conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    f"SERVER={os.getenv('AZURE_SERVER')};"
    f"DATABASE={os.getenv('AZURE_DATABASE')};"
    f"UID={os.getenv('AZURE_USER')};"
    f"PWD={os.getenv('AZURE_PASSWORD')};"
    "Encrypt=yes;"
    "TrustServerCertificate=yes;"
    "Connection Timeout=30;"
)

cur = azure_conn.cursor()
cur.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='VW_WinOrdeTraba' ORDER BY ORDINAL_POSITION")
src = [r[0] for r in cur.fetchall()]
azure_conn.close()

mysql_conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST'),
    port=int(os.getenv('MYSQL_PORT', '3306')),
    user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASSWORD'),
    database=os.getenv('MYSQL_DATABASE')
)
cur2 = mysql_conn.cursor()
cur2.execute("SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema='BD_Phoenix' AND table_name='VW_WinORdeTraba' ORDER BY ORDINAL_POSITION")
dst = [r[0] for r in cur2.fetchall()]
mysql_conn.close()

print('SOURCE count:', len(src))
print('DEST count:', len(dst))
print('SOURCE columns:', src)
print('DEST columns:', dst)
