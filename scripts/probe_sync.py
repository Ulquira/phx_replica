import os
import sys
import pyodbc
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

print('loading env ok')
print('azure server', os.getenv('AZURE_SERVER'))
conn_str = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    f"SERVER={os.getenv('AZURE_SERVER')};"
    f"DATABASE={os.getenv('AZURE_DATABASE')};"
    f"UID={os.getenv('AZURE_USER')};"
    f"PWD={os.getenv('AZURE_PASSWORD')};"
    "Encrypt=yes;"
    "TrustServerCertificate=yes;"
    "Connection Timeout=30;"
)
print('connecting azure...')
azure_conn = pyodbc.connect(conn_str)
print('azure connected')
cur = azure_conn.cursor()
cur.execute("SELECT TOP 3 * FROM [dbo].[VW_WinOrdeTraba]")
rows = cur.fetchall()
print('azure rows', len(rows))
for row in rows[:1]:
    print(row)
azure_conn.close()
print('connecting mysql...')
mysql_conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST'),
    port=int(os.getenv('MYSQL_PORT', '3306')),
    user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASSWORD'),
    database=os.getenv('MYSQL_DATABASE')
)
print('mysql connected')
cur2 = mysql_conn.cursor()
cur2.execute('SELECT 1')
print('mysql select', cur2.fetchone())
mysql_conn.close()
