import sqlite3
from datetime import date, timedelta

DB = r'C:\GNC_API\data\gnc.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

fecha_hasta = date.today()
meses = 3
fecha_desde = date(fecha_hasta.year - (1 if fecha_hasta.month <= meses else 0),
                   ((fecha_hasta.month - meses - 1) % 12) + 1, 1)
f_desde = fecha_desde.isoformat()
f_hasta = fecha_hasta.isoformat()

print(f"Rango: {f_desde} → {f_hasta}")

try:
    rows = conn.execute(f"""
        SELECT r.codigoresponsable             AS id,
               r.apellidoynombre               AS nombre,
               r.nombre                        AS nombre_propio,
               r.apellido,
               COALESCE(pe.sector, r.codsector) AS sector,
               r.cargo,
               r.comentarios,
               COALESCE(pe.legajo, '')          AS legajo,
               COALESCE(SUM(
                   CASE WHEN substr(h.fecha,1,10) BETWEEN ? AND ?
                             AND h.horastrabajadas > 0
                        THEN h.horastrabajadas END
               ), 0) AS hs_periodo
        FROM _v_responsables r
        LEFT JOIN _personal_ext pe ON pe.access_id = r.codigoresponsable
        LEFT JOIN HorasPorFecha h
               ON h."códigoresponsable" = r.codigoresponsable
        WHERE (r.codigoresponsable BETWEEN 1 AND 2000 OR pe.access_id IS NOT NULL)
          AND COALESCE(pe.sector, r.codsector) NOT IN (0, 99)
          AND r.apellidoynombre NOT IN
              ('-','NN','DbAdministrator','AgenciaTaller','AgenciaAdmin','IRAM','Batiplane')
          AND COALESCE(pe.sector, r.codsector) <> 97
        GROUP BY r.codigoresponsable
        ORDER BY hs_periodo DESC, r.apellidoynombre
        LIMIT 5
    """, (f_desde, f_hasta)).fetchall()
    print(f"OK — {len(rows)} filas")
    for r in rows:
        print(f"  {dict(r)}")
except Exception as e:
    print(f"ERROR: {e}")

conn.close()
