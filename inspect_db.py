"""
Inspección completa de gnc.db con contexto de la app.
Genera data/db_report.txt con schema, uso, nombres problemáticos y datos de muestra.
"""
import sqlite3, sys, re
sys.stdout.reconfigure(encoding='utf-8')

DB = r'C:\GNC_API\data\gnc.db'
OUT = r'C:\GNC_API\data\db_report.txt'

# ── Contexto de la app ─────────────────────────────────���───────────────────────

# Tablas que main.py usa activamente (extraído de los queries)
USADAS = {
    'Clientes':                  'Maestro clientes',
    'NombreDePiezas':            'Maestro piezas (nombre + cliente)',
    'PesosDePiezas':             'Maestro piezas (peso por variante)',
    'Materiales':                'Lookup materiales',
    'Responsables':              'Lookup operarios/responsables',
    'Modelos':                   'Maestro modelos de moldeo',
    'PiezasPorModelo':           'Relacion modelo ↔ pieza',
    'MovimientoModelos':         'Historial movimientos de modelos',
    'FotosDeModelos':            'Fotos de modelos',
    'PesosDeMoldes':             'Peso de moldes',
    'PartesDeModelo':            'Partes componentes de modelo',
    'PartesPorModelo':           'Relacion modelo ↔ parte',
    'Pedidos':                   'Cabecera pedidos de clientes',
    'ItemDetallePedido':         'Items de pedido (pieza + cantidad)',
    'Trabajos':                  'OT producción (pieza + cantidades prod/rech/rep)',
    'ItemDetalle':               'Items entregados por remito',
    'ItemDevolución':            'Devoluciones de clientes (con columnas de defecto)',
    'SoluciónDevolución':        'Descripción libre de devolución',
    'SoluciónRechazoInterno':    'Causa de rechazo interno por OT',
    'SoluciónReparacionInterna': 'Tipo de reparación interna por OT',
    'RechDevol':                 'Rechazos y devoluciones agrupados',
    'Remitos':                   'Cabecera remitos de entrega',
    'Fundiciones':               'Colada (horas, kg scrap, campaña)',
    'FundiciónPorFecha':         'Fecha por colada',
    'ItemProducción':            'Producción por pieza y colada',
    'HorasPorFecha':             'Horas trabajadas por fecha',
    'TipoCondicionPago':         'Lookup condición de pago',
    'TiposDocumento':            'Lookup tipos de documento',
    'Documentos':                'Reclamos / no conformidades',
    'ItemReclamo':               'Items de reclamo',
    'DocumentoReclamo':          'Archivos adjuntos a reclamo',
    'ModificacionesStock':       'Ajustes de stock',
    'MovimientosDeStock':        'Movimientos de stock',
    'TiposModelo':               'Lookup tipos de modelo',
    'InfTécnicaPieza':           'Ficha técnica de pieza',
    'InfTécnicaCalidad':         'Informe calidad por pieza',
    'InfTécnicaMoldeo':          'Informe moldeo por pieza',
    'InfTécnicaColada':          'Informe colada por pieza',
    'InfTécnicaNoyería':         'Informe noyería por pieza',
    'Estados':                   'Lookup estados de OT/pedido',
    'InformesMateriales':        'Composición química por colada',
    'InformesEspesores':         'Espesores medidos por colada',
    'tblInformesControlCalidad': 'Control de calidad histórico',
    'FundiciónPorFecha':         'Fecha de cada colada',
    'FechasFundición':           'Alternativo: fechas de colada',
    'Fundiciones2':              'Coladas secundarias (mismo schema)',
    '_table_names':              'Interno app: mapeo nombres de tabla',
    '_sync_log':                 'Interno app: historial sincronización',
}

# FK implícitas conocidas (origen → destino)
FK_CONOCIDAS = [
    ('Pedidos',              'códigocliente',      'Clientes',           'códigocliente'),
    ('Pedidos',              'condpago',           'TipoCondicionPago',  'idcondpago'),
    ('ItemDetallePedido',    'idpedido',           'Pedidos',            'idpedido'),
    ('ItemDetallePedido',    'idpieza',            'NombreDePiezas',     'id'),
    ('Trabajos',             'iditempedido',       'ItemDetallePedido',  'iditempedido'),
    ('Trabajos',             'idpesopieza',        'PesosDePiezas',      'códpieza'),
    ('Trabajos',             'iditemproducción',   'ItemProducción',     'iditemproducción'),
    ('Trabajos',             'códigoresponsable1', 'Responsables',       'códigoresponsable'),
    ('Trabajos',             'códigoresponsable2', 'Responsables',       'códigoresponsable'),
    ('Trabajos',             'códmaterial',        'Materiales',         'especificaciónmaterial'),
    ('ItemDetalle',          'idnroremito',        'Remitos',            'idnroremito'),
    ('ItemDetalle',          'iditemtrabajo',      'Trabajos',           'iditemtrabajo'),
    ('ItemDetalle',          'códpieza',           'PesosDePiezas',      'códpieza'),
    ('ItemDevolución',       'códpieza',           'PesosDePiezas',      'códpieza'),
    ('SoluciónRechazoInterno',    'IdItemTrabajo', 'Trabajos',           'iditemtrabajo'),
    ('SoluciónReparacionInterna', 'IdItemTrabajo', 'Trabajos',           'iditemtrabajo'),
    ('SoluciónDevolución',   'iddevol',            'ItemDevolución',     'iddevol'),
    ('NombreDePiezas',       'códcliente',         'Clientes',           'códigocliente'),
    ('PesosDePiezas',        'nombredepiezasid_',  'NombreDePiezas',     'id'),
    ('PiezasPorModelo',      'códmodelo',          'Modelos',            'códigomodelo'),
    ('PiezasPorModelo',      'códpieza',           'NombreDePiezas',     'id'),
    ('MovimientoModelos',    'códigomodelo',       'Modelos',            'códigomodelo'),
    ('FotosDeModelos',       'códigomodelo',       'Modelos',            'códigomodelo'),
    ('FundiciónPorFecha',    'códfundición',       'Fundiciones',        'códfundición'),
    ('ItemProducción',       'códfundición',       'Fundiciones',        'códfundición'),
    ('ItemProducción',       'códpieza',           'PesosDePiezas',      'códpieza'),
    ('InformesMateriales',   'códfundición',       'Fundiciones',        'códfundición'),
    ('Remitos',              'códigocliente',      'Clientes',           'códigocliente'),
    ('Documentos',           'códigocliente',      'Clientes',           'códigocliente'),
    ('Documentos',           'idtipodoc',          'TiposDocumento',     'idtipodoc'),
    ('ItemReclamo',          'refpieza',           'NombreDePiezas',     'id'),
    ('ModificacionesStock',  'iditemtrabajo',      'Trabajos',           'iditemtrabajo'),
    ('HorasPorFecha',        'fecha',              '(fecha calendario)', ''),
]

# Nombres problemáticos (regla: tiene tilde, espacio, mayúscula inconsistente, trailing _)
def _nombre_feo(s):
    problemas = []
    if re.search(r'[áéíóúüñÁÉÍÓÚÜÑ]', s):  problemas.append('tilde')
    if ' ' in s:                              problemas.append('espacio')
    if s.endswith('_'):                       problemas.append('trailing_')
    if s != s.lower() and s != s.upper() and not s[0].isupper():
        problemas.append('mayus_inconsistente')
    return problemas

# ── Inspección ─────────────────────────────────────────────────────────────────

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

tables = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
).fetchall()]

# FK por tabla origen (para mostrar junto a la tabla)
fk_por_tabla = {}
for fk in FK_CONOCIDAS:
    fk_por_tabla.setdefault(fk[0], []).append(fk)

lines = []
lines.append('=' * 70)
lines.append(f'REPORTE DE INSPECCIÓN — gnc.db')
lines.append(f'Total tablas: {len(tables)}')
lines.append('=' * 70)

# Resumen de nombres problemáticos en tablas
lines.append('\n── TABLAS CON NOMBRES PROBLEMÁTICOS ──��───────────────────────────────')
for tbl in tables:
    p = _nombre_feo(tbl)
    if p:
        usado = '✓ usa app' if tbl in USADAS else '  no usada'
        lines.append(f'  {usado}  {tbl}  [{", ".join(p)}]')

lines.append('\n── TABLAS NO IDENTIFICADAS EN main.py ────────────────────────────────')
for tbl in tables:
    if tbl not in USADAS and not tbl.startswith('_'):
        count = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
        lines.append(f'  {tbl}  ({count:,} filas)')

# Detalle por tabla
lines.append('\n\n' + '=' * 70)
lines.append('DETALLE POR TABLA')
lines.append('=' * 70)

for tbl in sorted(tables):
    count = conn.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
    cols  = conn.execute(f'PRAGMA table_info("{tbl}")').fetchall()
    idxs  = conn.execute(f'PRAGMA index_list("{tbl}")').fetchall()

    uso   = USADAS.get(tbl, '— no identificada en main.py')
    pfx   = '✓' if tbl in USADAS else '?'
    tbl_p = _nombre_feo(tbl)

    lines.append(f'\n{"─"*60}')
    lines.append(f'{pfx} {tbl}  ({count:,} filas)')
    lines.append(f'  Uso: {uso}')
    if tbl_p:
        lines.append(f'  !! Nombre problemático: {", ".join(tbl_p)}')

    # Columnas
    lines.append(f'  Columnas ({len(cols)}):')
    for c in cols:
        pk   = ' PK' if c['pk'] else ''
        nn   = ' NOT NULL' if c['notnull'] else ''
        dft  = f' DEFAULT {c["dflt_value"]}' if c['dflt_value'] is not None else ''
        col_p = _nombre_feo(c['name'])
        warn = f'  !! [{", ".join(col_p)}]' if col_p else ''
        lines.append(f'    {c["name"]:<38} {c["type"]:<10}{pk}{nn}{dft}{warn}')

    # % nulos
    if count > 0:
        null_cols = []
        for c in cols:
            n = conn.execute(f'SELECT COUNT(*) FROM "{tbl}" WHERE "{c["name"]}" IS NULL').fetchone()[0]
            if n > 0:
                null_cols.append(f'{c["name"]}={n/count*100:.0f}%')
        if null_cols:
            lines.append(f'  Nulos: {", ".join(null_cols)}')

    # Índices
    if idxs:
        for idx in idxs:
            idx_cols = conn.execute(f'PRAGMA index_info("{idx["name"]}")').fetchall()
            col_names = [ic['name'] for ic in idx_cols]
            lines.append(f'  Índice: {idx["name"]}  ({", ".join(col_names)})  unique={idx["unique"]}')
    else:
        if count > 1000:
            lines.append(f'  !! Sin índices — tabla grande ({count:,} filas)')

    # FK conocidas
    if tbl in fk_por_tabla:
        lines.append(f'  FK (implícitas):')
        for fk in fk_por_tabla[tbl]:
            lines.append(f'    {fk[1]}  →  {fk[2]}.{fk[3]}')

    # Muestra de datos
    try:
        sample = conn.execute(f'SELECT * FROM "{tbl}" ORDER BY rowid DESC LIMIT 2').fetchall()
        if sample:
            lines.append(f'  Muestra (últimas 2 filas):')
            cnames = [c['name'] for c in cols]
            for row in sample:
                d = {k: v for k, v in zip(cnames, row) if v is not None and v != ''}
                lines.append('    ' + str(d)[:250])
    except Exception as e:
        lines.append(f'  [error leyendo muestra: {e}]')

conn.close()

with open(OUT, 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

print(f'Reporte guardado en {OUT}  ({len(lines)} líneas)')
