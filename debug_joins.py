import pyodbc
import sys

conn_str = "DRIVER={ODBC Driver 18 for SQL Server};SERVER=tcp:phoenixwin.database.windows.net,1433;DATABASE=WIN.Phoenix;UID=Consulta;PWD=M@chu#Pichu710_;Encrypt=yes;TrustServerCertificate=yes;"

try:
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()

    print("--- ORDEN 1 ---")
    cursor.execute("SELECT TOP 1 Cuadrilla FROM VW_WinOrdeTraba WHERE Estado = 'En camino'")
    cuadrilla_row = cursor.fetchone()
    if not cuadrilla_row:
        print("No hay ordenes en camino")
        sys.exit(0)
        
    cuadrilla_nombre = cuadrilla_row[0]
    print(f"Cuadrilla en Orden: [{cuadrilla_nombre}]")

    print("--- BUSCANDO EN TABLA CUADRILLAS ---")
    cursor.execute("SELECT CuadriId, Nombre FROM Cuadrillas WHERE Nombre = ?", cuadrilla_nombre)
    row_c = cursor.fetchone()
    if row_c:
        print(f"Encontrado en Cuadrillas! ID: {row_c[0]}")
        
        print("--- BUSCANDO POSICION ---")
        cursor.execute("SELECT Latitud, Longitud FROM VW_CuadriUltiPosi WHERE CuadrillaId = ?", row_c[0])
        pos = cursor.fetchone()
        if pos:
            print(f"Posicion encontrada: {pos[0]}, {pos[1]}")
        else:
            print("LA CUADRILLA EXISTE PERO NO TIENE POSICION EN VW_CuadriUltiPosi")
    else:
        print("¡ERROR EN EL PRIMER JOIN! NO EXISTE ESA CUADRILLA EN LA TABLA CUADRILLAS")
        print("Esto significa que el texto en la orden de trabajo es diferente al de la tabla Cuadrillas (espacios extra, tildes, etc)")

except Exception as e:
    print(f"Error general: {e}")
