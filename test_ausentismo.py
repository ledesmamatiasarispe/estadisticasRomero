import sqlite3
conn = sqlite3.connect(r'C:\GNC_API\data\gnc.db')
conn.row_factory = sqlite3.Row

# Ver cuántos registros hay
total = conn.execute('SELECT COUNT(*) FROM _personal_ausentismo').fetchone()[0]
print('Total registros ausentismo:', total)

# Ver primeros registros si los hay
rows = conn.execute('SELECT * FROM _personal_ausentismo LIMIT 5').fetchall()
for r in rows:
    print(dict(r))

# Ver estructura de la tabla
cols = [r[1] for r in conn.execute('PRAGMA table_info(_personal_ausentismo)').fetchall()]
print('Columnas:', cols)
conn.close()
