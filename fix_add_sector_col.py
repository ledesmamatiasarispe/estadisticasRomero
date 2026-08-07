import sqlite3
conn = sqlite3.connect(r'C:\GNC_API\data\gnc.db')

cols = [r[1] for r in conn.execute('PRAGMA table_info(_personal_ext)').fetchall()]
print('Columnas actuales:', cols)

if 'sector' not in cols:
    conn.execute('ALTER TABLE _personal_ext ADD COLUMN sector INTEGER DEFAULT NULL')
    conn.commit()
    print('Columna sector agregada.')
else:
    print('Columna sector ya existe.')

conn.close()
