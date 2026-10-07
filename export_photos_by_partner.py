import os
import re
import base64
import shutil
from pathlib import Path
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = Path(__file__).with_name("fotos_por_partner")

def sanitize_name(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', '_', name.strip()) if name else "SIN_NOMBRE"

def export_photos():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Limpiar todos los archivos .jpg previos recursivamente
    for f in OUTPUT_DIR.rglob("*.jpg"):
        try:
            f.unlink()
        except Exception:
            pass
    
    conn = mysql.connector.connect(
        host=os.getenv('MYSQL_HOST', 'phx-win-mysql-9508.mysql.database.azure.com'),
        port=int(os.getenv('MYSQL_PORT', 3306)),
        user=os.getenv('MYSQL_USER', 'phxadmin'),
        password=os.getenv('MYSQL_PASSWORD', 'WinTelecom@2026!'),
        database=os.getenv('MYSQL_DATABASE', 'BD_Phoenix')
    )
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT Partner, Documento, Nombre_Tecnico_Limpio, Foto_Img, foto_aprobada 
        FROM vw_info_cuadrillas 
        WHERE Foto_Img IS NOT NULL AND Foto_Img <> ''
    """)
    rows = cursor.fetchall()
    
    total = len(rows)
    exported = 0
    partners_count = {}

    for r in rows:
        val_aprobada = str(r.get('foto_aprobada') or '').strip().upper()
        # Filtrar solo las DESAPROBADAS (omitir APROBADAS)
        if val_aprobada in ['1', 'Y', 'YES', 'TRUE']:
            continue

        partner = sanitize_name(r['Partner'] or 'SIN_PARTNER')
        doc = sanitize_name(r['Documento'] or 'SIN_DOC')
        nombre = sanitize_name(r['Nombre_Tecnico_Limpio'] or 'TECNICO')

        partner_dir = OUTPUT_DIR / partner
        partner_dir.mkdir(parents=True, exist_ok=True)

        raw_b64 = r['Foto_Img'].split(",", 1)[1] if "," in r['Foto_Img'] else r['Foto_Img']
        try:
            img_bytes = base64.b64decode(raw_b64)
            file_path = partner_dir / f"DESAPROBADA_{doc}_{nombre}.jpg"
            file_path.write_bytes(img_bytes)
            exported += 1
            partners_count[partner] = partners_count.get(partner, 0) + 1
        except Exception as e:
            print(f"Error exportando {doc}: {e}")

    cursor.close()
    conn.close()
    
    print(f"Total fotos DESAPROBADAS exportadas: {exported} (de {total} con foto)")
    print(f"Total carpetas de partners con fotos desaprobadas: {len(partners_count)}")
    print(f"Ruta de salida: {OUTPUT_DIR.resolve()}")

if __name__ == "__main__":
    export_photos()
