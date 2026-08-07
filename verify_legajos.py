import sqlite3
conn = sqlite3.connect(r'C:\GNC_API\data\gnc.db')
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT pe.access_id, pe.legajo, pe.apellido, pe.nombre "
    "FROM _personal_ext pe ORDER BY CAST(pe.legajo AS INTEGER), pe.legajo"
).fetchall()
print(f"{'Access ID':>10}  {'Legajo':>6}  {'Apellido':<22} {'Nombre'}")
print("-"*55)
for r in rows:
    print(f"{r['access_id']:>10}  {r['legajo']:>6}  {r['apellido']:<22} {r['nombre']}")
print(f"\nTotal: {len(rows)}")
conn.close()
