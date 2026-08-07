import sqlite3
conn = sqlite3.connect(r'C:\GNC_API\data\gnc.db')
conn.row_factory = sqlite3.Row

# Simular el endpoint /api/personal/1110/rechazos?anio_desde=2020
# para ver si tiene la estructura esperada con es_moldeador
try:
    rows = conn.execute("""
        SELECT r.codigoresponsable AS id
        FROM _v_responsables r
        WHERE r.codigoresponsable = 1110
    """).fetchone()
    print("Persona view OK:", dict(rows) if rows else None)
except Exception as e:
    print("Error view:", e)

# Verificar el endpoint de rechazos directamente
try:
    # Buscar el endpoint en main.py para entender qué retorna
    import sys
    sys.path.insert(0, r'C:\GNC_API')
    # Verificar que la tabla de rechazos tenga la columna es_moldeador
    cols_trabajos = [r[1] for r in conn.execute("PRAGMA table_info(Trabajos)").fetchall()]
    print("Columnas Trabajos:", [c for c in cols_trabajos if 'molde' in c.lower() or 'rech' in c.lower()])
except Exception as e:
    print("Error:", e)

conn.close()
