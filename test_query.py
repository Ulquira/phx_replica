import pyodbc

conn_str = "DRIVER={ODBC Driver 18 for SQL Server};SERVER=tcp:phoenixwin.database.windows.net,1433;DATABASE=WIN.Phoenix;UID=Consulta;PWD=M@chu#Pichu710_;Encrypt=yes;TrustServerCertificate=yes;"
try:
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()
    
    print("1. Buscando registros 'En camino'...")
    cursor.execute("SELECT TOP 5 Estado, Cuadrilla FROM VW_WinOrdeTraba WHERE Estado LIKE '%camino%'")
    rows1 = cursor.fetchall()
    print(rows1)

    print("\n2. Probando cruce con Cuadrillas...")
    cursor.execute("""
        SELECT TOP 5 t.Estado, t.Cuadrilla, c.Nombre, c.Cuadrillaid 
        FROM VW_WinOrdeTraba t 
        LEFT JOIN Cuadrillas c ON t.Cuadrilla = c.Nombre 
        WHERE t.Estado LIKE '%camino%'
    """)
    rows2 = cursor.fetchall()
    print(rows2)

    print("\n3. Probando cruce completo con VW_UltiTecniHistoPosi...")
    cursor.execute("""
        SELECT TOP 5 c.Cuadrillaid, p.Tecnicoid, p.Latitud, p.Longitud 
        FROM VW_WinOrdeTraba t 
        LEFT JOIN Cuadrillas c ON t.Cuadrilla = c.Nombre 
        LEFT JOIN VW_UltiTecniHistoPosi p ON c.Cuadrillaid = p.Tecnicoid 
        WHERE t.Estado LIKE '%camino%'
    """)
    rows3 = cursor.fetchall()
    print(rows3)

except Exception as e:
    print(f"Error: {e}")
