import os
import pyodbc
from dotenv import load_dotenv

load_dotenv()

conn_str = (
    "DRIVER={ODBC Driver 18 for SQL Server};"
    f"SERVER={os.getenv('AZURE_SERVER', 'tcp:phoenixwin.database.windows.net,1433')};"
    f"DATABASE={os.getenv('AZURE_DATABASE', 'WIN.Phoenix')};"
    f"UID={os.getenv('AZURE_USER')};"
    f"PWD={os.getenv('AZURE_PASSWORD')};"
    "Encrypt=yes;"
    "TrustServerCertificate=yes;"
)
try:
    conn = pyodbc.connect(conn_str)
    cursor = conn.cursor()

    print("--- PRUEBA DE CONSULTA 'EN CAMINO' ---")
    query = """
        SELECT TOP 5 
            t.Estado, 
            t.Cuadrilla,
            c.Cuadrillaid,
            p.TecniId,
            p.Latitud, 
            p.Longitud,
            CAST(p.Latitud AS VARCHAR(50)) + ',' + CAST(p.Longitud AS VARCHAR(50)) AS Geo
        FROM [dbo].[VW_WinOrdeTraba] t
        LEFT JOIN [dbo].[Cuadrillas] c ON t.Cuadrilla = c.Nombre
        LEFT JOIN [dbo].[VW_UltiTecniHistoPosi] p ON c.Cuadrillaid = p.TecniId
        WHERE t.Estado = 'En camino'
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    for row in rows:
        print(row)

except Exception as e:
    print(f"Error: {e}")
