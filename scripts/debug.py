import pyodbc, os
conn_str = 'DRIVER={ODBC Driver 18 for SQL Server};SERVER=tcp:phoenixwin.database.windows.net,1433;DATABASE=WIN.Phoenix;UID=Consulta;PWD=M@chu#Pichu710_;Encrypt=yes;TrustServerCertificate=yes;'
conn = pyodbc.connect(conn_str)
cursor = conn.cursor()

print('--- ORDEN 1 ---')
cursor.execute(" SELECT TOP 1 Cuadrilla FROM VW_WinOrdeTraba WHERE Estado = En
