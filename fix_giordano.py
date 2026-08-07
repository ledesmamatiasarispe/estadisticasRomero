import sqlite3
conn = sqlite3.connect(r'C:\GNC_API\data\gnc.db')
# Verificar columna correcta
cols = [r[1] for r in conn.execute('PRAGMA table_info(Responsables)').fetchall()]
print("Columnas:", [c for c in cols if 'sect' in c.lower() or 'codigo' in c.lower()])

# Sector 1 = Producción (activo, no desvinculado)
conn.execute('UPDATE Responsables SET "códsector"=1 WHERE "códigoresponsable"=9001')
conn.commit()
row = conn.execute('SELECT "códigoresponsable", apellidoynombreresponsable, "códsector" FROM Responsables WHERE "códigoresponsable"=9001').fetchone()
print("Resultado:", row)
conn.close()
