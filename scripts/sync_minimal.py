import os
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
cur.execute("SELECT TOP 1 OrdenId, Estado FROM [dbo].[VW_WinOrdeTraba]")
row = cur.fetchone()
azure_conn.close()

print('row', row)

mysql_conn = mysql.connector.connect(
    host=os.getenv('MYSQL_HOST'),
    port=int(os.getenv('MYSQL_PORT', '3306')),
    user=os.getenv('MYSQL_USER'),
    password=os.getenv('MYSQL_PASSWORD'),
    database=os.getenv('MYSQL_DATABASE')
)
cur2 = mysql_conn.cursor()
cur2.execute("INSERT INTO VW_WinORdeTraba (OrdenId, Estado) VALUES (%s, %s)", (row[0], row[1]))
mysql_conn.commit()
print('inserted')
mysql_conn.close()
