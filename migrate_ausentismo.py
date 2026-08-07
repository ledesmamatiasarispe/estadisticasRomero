"""migrate_ausentismo.py — Importa hoja DATOS de Faltas Personal.xlsm a _personal_ausentismo."""
import openpyxl
import sqlite3

FILE = r"M:\ArchivosCompartidosResguardo\0_Jose Romero e hijos SRL\1_Produccion\2 - Laboratorio\Estadísticas\Indicadores de calidad\Faltas Personal.xlsm"
DB   = r"C:\GNC_API\data\gnc.db"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# Construir mapa legajo_real (TEXT) → access_id (INTEGER)
leg_map = {
    r["legajo"]: r["access_id"]
    for r in conn.execute("SELECT access_id, legajo FROM _personal_ext").fetchall()
    if r["legajo"]
}
print(f"Legajos mapeados: {len(leg_map)}")

wb  = openpyxl.load_workbook(FILE, read_only=True, data_only=True)
ws  = wb["DATOS"]

ok = skip_leg = skip_row = 0
errors = []

for i, row in enumerate(ws.iter_rows(values_only=True)):
    if i == 0:
        continue  # cabecera

    legajo_raw, _, _, fecha_raw, _, dias_lab, enf, fca, fsa, acc, vac, _, _, _, _, llt, ret, _ = row

    if legajo_raw is None or fecha_raw is None:
        skip_row += 1
        continue

    legajo_str = str(int(legajo_raw)) if isinstance(legajo_raw, (int, float)) else str(legajo_raw).strip()
    access_id  = leg_map.get(legajo_str)

    if access_id is None:
        if legajo_str not in [e for e in errors]:
            errors.append(legajo_str)
        skip_leg += 1
        continue

    # Fecha → YYYY-MM
    if hasattr(fecha_raw, "strftime"):
        fecha = fecha_raw.strftime("%Y-%m")
    else:
        fecha = str(fecha_raw)[:7]

    def n(v): return float(v) if v not in (None, "") else 0.0
    def ni(v): return int(v) if v not in (None, "") else 0

    try:
        conn.execute("""
            INSERT INTO _personal_ausentismo
                (access_id, fecha, dias_laborables, enfermedad, falta_con_aviso,
                 falta_sin_aviso, accidente, vacaciones, llegada_tarde, retiro_anticipado)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(access_id, fecha) DO UPDATE SET
                dias_laborables   = excluded.dias_laborables,
                enfermedad        = excluded.enfermedad,
                falta_con_aviso   = excluded.falta_con_aviso,
                falta_sin_aviso   = excluded.falta_sin_aviso,
                accidente         = excluded.accidente,
                vacaciones        = excluded.vacaciones,
                llegada_tarde     = excluded.llegada_tarde,
                retiro_anticipado = excluded.retiro_anticipado
        """, (access_id, fecha,
              ni(dias_lab), n(enf), n(fca), n(fsa), n(acc), n(vac),
              ni(llt), ni(ret)))
        ok += 1
    except Exception as e:
        print(f"  ERROR fila {i}: {e}")

conn.commit()
wb.close()
conn.close()

print(f"\nImportados : {ok}")
print(f"Sin mapeo  : {skip_leg}  (legajos no encontrados: {sorted(set(errors))})")
print(f"Filas vacías: {skip_row}")
