import sqlite3
conn = sqlite3.connect(r'C:\GNC_API\data\gnc.db')
row = conn.execute('SELECT * FROM _responsables_extra WHERE codigoresponsable=9001').fetchone()
print('_responsables_extra:', row)
conn.execute('DELETE FROM Responsables WHERE "códigoresponsable"=9001')
conn.commit()
remaining = conn.execute('SELECT "códigoresponsable" FROM Responsables WHERE "códigoresponsable"=9001').fetchone()
print('En Responsables:', remaining)
conn.close()
