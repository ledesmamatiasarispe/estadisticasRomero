"""migrate_fotos_psp.py — Copia fotos/informes de la app original y vincula con registros PSP en la DB."""
import sqlite3, shutil, unicodedata
from pathlib import Path

SRC = Path(r"M:\ArchivosCompartidosResguardo\0_Jose Romero e hijos SRL\3_ISO 9001- 2024\FUNDICIÓN ROMERO - ISO 9001- 2024\12. Documentos\Proveedores\provedores pyton\informes_calidad")
DST = Path(r"C:\GNC_API\data\informes_psp")
DB  = Path(r"C:\GNC_API\data\gnc.db")


def safe_file(value):
    text = unicodedata.normalize("NFD", str(value or "")).encode("ascii", "ignore").decode()
    text = text.lower().strip().replace(" ", "_")
    return "".join(ch for ch in text if ch.isalnum() or ch in ("_", "-")) or "sin_dato"


DST.mkdir(parents=True, exist_ok=True)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
psps = conn.execute("SELECT id, proveedor, producto, partida FROM _prov_psp").fetchall()

# lookup: (prov_safe, prod_safe, partida_safe) -> lista de ids PSP
lookup: dict[tuple, list[int]] = {}
for r in psps:
    key = (safe_file(r["proveedor"]), safe_file(r["producto"]), safe_file(r["partida"]))
    lookup.setdefault(key, []).append(r["id"])

copied = 0
skipped = 0
linked = 0
no_match = []

for src_file in sorted(SRC.rglob("*")):
    if not src_file.is_file():
        continue
    parts = src_file.relative_to(SRC).parts
    if len(parts) != 3:
        continue  # estructura esperada: prov_safe/prod_safe/archivo.ext

    prov_safe, prod_safe, filename = parts
    stem = Path(filename).stem

    dst_dir = DST / prov_safe / prod_safe
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst_file = dst_dir / filename

    if dst_file.exists():
        skipped += 1
    else:
        shutil.copy2(src_file, dst_file)
        copied += 1

    rel_path = f"{prov_safe}/{prod_safe}/{filename}"
    key = (prov_safe, prod_safe, stem)
    ids = lookup.get(key, [])

    if not ids:
        no_match.append(f"  {rel_path}")
    else:
        for psp_id in ids:
            cur = conn.execute(
                "UPDATE _prov_psp SET tiene_informe=1, archivo_informe=? "
                "WHERE id=? AND (archivo_informe='' OR archivo_informe IS NULL)",
                (rel_path, psp_id),
            )
            linked += cur.rowcount

conn.commit()
conn.close()

print(f"Archivos copiados : {copied}")
print(f"Ya existían       : {skipped}")
print(f"Registros PSP vinculados: {linked}")
if no_match:
    print(f"\nSin coincidencia en DB ({len(no_match)} archivos):")
    for m in no_match:
        print(m)
else:
    print("Todos los archivos coincidieron con registros PSP.")
