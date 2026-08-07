"""migrate_proveedores.py — Importa datos JSON de la app Proveedores → gnc.db.

Ejecutar UNA vez manualmente:
    python C:\GNC_API\migrate_proveedores.py

Es idempotente: limpia las tablas _prov_* antes de insertar.
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

SRC_DATA = Path(r"M:\ArchivosCompartidosResguardo\0_Jose Romero e hijos SRL"
                r"\3_ISO 9001- 2024\FUNDICIÓN ROMERO - ISO 9001- 2024"
                r"\12. Documentos\Proveedores\provedores pyton\data")
DB_PATH  = Path(r"C:\GNC_API\data\gnc.db")


def load_json(filename):
    path = SRC_DATA / filename
    if not path.exists():
        print(f"  [WARN] No encontrado: {path}")
        return [], []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("headers", []), data.get("rows", [])


def to_iso(fecha_str):
    """'07/01/2019' → '2019-01-07'. Devuelve original si no puede parsear."""
    s = str(fecha_str or "").strip()
    if not s:
        return ""
    if "/" in s:
        parts = s.split("/")
        if len(parts) == 3:
            try:
                return f"{parts[2]}-{int(parts[1]):02d}-{int(parts[0]):02d}"
            except ValueError:
                pass
    return s


def si_no(value):
    v = str(value or "").strip().lower()
    return 1 if v in ("si","s","sí","yes","true","1") else 0


def main():
    if not DB_PATH.exists():
        print(f"ERROR: No se encontró {DB_PATH}")
        print("  Asegúrate de que el servidor GNC esté inicializado al menos una vez.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = OFF")

    # ── Crear tablas si no existen (por si el servidor no ha corrido aún) ──────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS _prov_grupos (
            id TEXT PRIMARY KEY, nombre TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS _prov_unidades (
            id TEXT PRIMARY KEY, descripcion TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS _prov_proveedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL,
            rubros TEXT DEFAULT '', grupos TEXT DEFAULT '',
            nivel TEXT DEFAULT 'sin datos en el ultimo año',
            valoracion TEXT DEFAULT '#DIV/0!',
            cant_recibida TEXT DEFAULT '0', cant_observada TEXT DEFAULT '0',
            cant_prueba TEXT DEFAULT '0', vencimiento TEXT DEFAULT '-',
            observaciones TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS _prov_productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL,
            descripcion TEXT DEFAULT '', rubro TEXT DEFAULT '', grupo TEXT DEFAULT '',
            proveedor_id INTEGER REFERENCES _prov_proveedores(id),
            proveedor_nombre TEXT DEFAULT '', cantidad_ref TEXT DEFAULT '',
            etp TEXT DEFAULT '', critico INTEGER DEFAULT 0, unidad TEXT DEFAULT 'Kg'
        );
        CREATE TABLE IF NOT EXISTS _prov_etp (
            codigo TEXT PRIMARY KEY, producto TEXT DEFAULT '',
            revision TEXT DEFAULT '', estado TEXT DEFAULT 'Vigente',
            detalle TEXT DEFAULT '', pdf_path TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS _prov_psp (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto TEXT NOT NULL, proveedor TEXT NOT NULL,
            grupo TEXT DEFAULT '', etp TEXT DEFAULT '',
            fecha TEXT NOT NULL, cant_recibida REAL NOT NULL DEFAULT 0,
            unidad TEXT DEFAULT 'Kg', cant_observada TEXT DEFAULT '-',
            motivo TEXT DEFAULT '-', tiene_remito INTEGER DEFAULT 1,
            control_visual INTEGER DEFAULT 1, tiene_informe INTEGER DEFAULT 0,
            partida TEXT DEFAULT '', remito TEXT DEFAULT '',
            observaciones TEXT DEFAULT '', archivo_informe TEXT DEFAULT '',
            created_at TEXT
        );
    """)
    conn.commit()

    # ── Limpiar en orden inverso a FKs ────────────────────────────────────────
    for t in ("_prov_psp","_prov_etp","_prov_productos","_prov_proveedores",
              "_prov_unidades","_prov_grupos"):
        conn.execute(f"DELETE FROM {t}")
    print("Tablas _prov_* vaciadas.")

    # ── Grupos ─────────────────────────────────────────────────────────────────
    headers, rows = load_json("grupos.json")
    # rows[i] = [nombre, codigo]  (orden en el JSON original)
    n_grupos = 0
    for row in rows:
        nombre = str(row[0] if len(row) > 0 else "").strip()
        codigo = str(row[1] if len(row) > 1 else "").strip()
        if not codigo or not nombre:
            continue
        conn.execute("INSERT OR IGNORE INTO _prov_grupos (id, nombre) VALUES (?,?)",
                     (codigo, nombre))
        n_grupos += 1
    print(f"  Grupos:     {n_grupos}")

    # ── Unidades (las predeterminadas ya fueron insertadas por _seed_lookups) ──
    # No hay archivo unidades.json en el directorio fuente; las 4 estándar ya existen.

    # ── Proveedores ─────────────────────────────────────────────────────────────
    headers, rows = load_json("backups/proveedores_20260513_152353.json")
    if not rows:
        # Intentar el archivo principal (puede no tener backups/)
        headers, rows = load_json("proveedores.json")

    # Índices según headers del JSON de proveedores:
    # 0=Nª, 1=Nombre, 2=Rubro, 3=Codigo Grupo/Rubro, 4=Nivel de Calidad,
    # 5=Valoración, 6=Cantidad Recibida, 7=Cantidad Observada,
    # 8=Cantidad Prueba, 9=Vencimiento Calificación, 10=Observaciones
    id_map = {}  # viejo_nro → nuevo_id (para productos FK)
    n_prov = 0
    for row in rows:
        viejo_id = str(row[0] if len(row) > 0 else "").strip()
        nombre   = str(row[1] if len(row) > 1 else "").strip()
        if not nombre:
            continue
        cur = conn.execute(
            "INSERT INTO _prov_proveedores "
            "(nombre, rubros, grupos, nivel, valoracion, cant_recibida, "
            "cant_observada, cant_prueba, vencimiento, observaciones) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (nombre,
             str(row[2] if len(row) > 2 else ""),
             str(row[3] if len(row) > 3 else ""),
             str(row[4] if len(row) > 4 else "sin datos en el ultimo año"),
             str(row[5] if len(row) > 5 else "#DIV/0!"),
             str(row[6] if len(row) > 6 else "0"),
             str(row[7] if len(row) > 7 else "0"),
             str(row[8] if len(row) > 8 else "0"),
             str(row[9] if len(row) > 9 else "-"),
             str(row[10] if len(row) > 10 else ""))
        )
        id_map[viejo_id] = cur.lastrowid
        n_prov += 1
    print(f"  Proveedores:{n_prov}")

    # ── Productos ──────────────────────────────────────────────────────────────
    # Índices del JSON de productos (de la documentación técnica):
    # 0=?, 1=Descripción, 2=Nombre, 3=Rubro, 4=Grupo, 5=?,
    # 6=Código proveedor (viejo), 7=Nombre proveedor, 8=Critico, 9=ETP,
    # 10=Unidad predeterminada
    # (el JSON real puede variar; leemos por header si está disponible)
    _, prod_rows = load_json("productos.json")
    if not prod_rows:
        # Intentar backup reciente
        for bf in sorted((SRC_DATA / "backups").glob("productos_*.json"), reverse=True):
            _, prod_rows = load_json(f"backups/{bf.name}")
            if prod_rows:
                break

    n_prod = 0
    for row in prod_rows:
        # Leemos por posición según la estructura real del JSON exportado
        # col 2 = nombre del producto (el más importante)
        nombre           = str(row[2]  if len(row) > 2  else "").strip()
        descripcion      = str(row[1]  if len(row) > 1  else "")
        rubro            = str(row[3]  if len(row) > 3  else "")
        grupo            = str(row[4]  if len(row) > 4  else "")
        viejo_prov_cod   = str(row[6]  if len(row) > 6  else "").strip()
        proveedor_nombre = str(row[7]  if len(row) > 7  else "")
        critico          = 1 if str(row[8] if len(row) > 8 else "").strip().lower() in ("si","s","sí","yes","true","1","x") else 0
        etp              = str(row[9]  if len(row) > 9  else "")
        unidad           = str(row[10] if len(row) > 10 else "Kg")
        if not nombre:
            continue
        # Buscar proveedor_id por nombre (más fiable que viejo código)
        prov_id = None
        if proveedor_nombre.strip():
            row_prov = conn.execute(
                "SELECT id FROM _prov_proveedores WHERE lower(nombre)=lower(?)",
                (proveedor_nombre.strip(),)
            ).fetchone()
            if row_prov:
                prov_id = row_prov[0]
        conn.execute(
            "INSERT INTO _prov_productos "
            "(nombre,descripcion,rubro,grupo,proveedor_id,proveedor_nombre,"
            "etp,critico,unidad) VALUES (?,?,?,?,?,?,?,?,?)",
            (nombre, descripcion, rubro, grupo, prov_id, proveedor_nombre,
             etp, critico, unidad)
        )
        n_prod += 1
    print(f"  Productos:  {n_prod}")

    # ── ETP ────────────────────────────────────────────────────────────────────
    # Usar el archivo ETP con más filas (etp.json suele estar desactualizado)
    _, etp_rows = load_json("etp.json")
    for bf in sorted((SRC_DATA / "backups").glob("etp_*.json")):
        _, candidate = load_json(f"backups/{bf.name}")
        if len(candidate) > len(etp_rows):
            etp_rows = candidate
    # Índices ETP: 0=Código, 1=Producto, 2=Revisión, 3=Estado, 4=Detalle, 5=PDF path
    n_etp = 0
    for row in etp_rows:
        codigo = str(row[0] if len(row) > 0 else "").strip()
        if not codigo:
            continue
        try:
            conn.execute(
                "INSERT OR REPLACE INTO _prov_etp (codigo,producto,revision,estado,detalle,pdf_path) "
                "VALUES (?,?,?,?,?,?)",
                (codigo,
                 str(row[1] if len(row) > 1 else ""),
                 str(row[2] if len(row) > 2 else ""),
                 str(row[3] if len(row) > 3 else "Vigente"),
                 str(row[4] if len(row) > 4 else ""),
                 str(row[5] if len(row) > 5 else ""))
            )
            n_etp += 1
        except Exception as e:
            print(f"  [WARN] ETP {codigo}: {e}")
    print(f"  ETPs:       {n_etp}")

    # ── PSP ────────────────────────────────────────────────────────────────────
    _, psp_rows = load_json("psp.json")
    if not psp_rows:
        for bf in sorted((SRC_DATA / "backups").glob("psp_*.json"), reverse=True):
            _, psp_rows = load_json(f"backups/{bf.name}")
            if psp_rows:
                break
    # Índices PSP (documentación técnica):
    # 0=Producto, 1=Proveedor, 2=Grupo/Rubro, 3=ETP, 4=Fecha,
    # 5=Cantidad Recibida, 6=Unidad, 7=Cant. Observada, 8=Motivo,
    # 9=¿Remito?, 10=¿Control Visual?, 11=¿Informe?, 12=Partida/Lote,
    # 13=Remito, 14=Observaciones, (15=Archivo informe si existe)
    n_psp = 0
    now_iso = datetime.now().isoformat()
    for row in psp_rows:
        producto  = str(row[0] if len(row) > 0 else "").strip()
        proveedor = str(row[1] if len(row) > 1 else "").strip()
        fecha_raw = str(row[4] if len(row) > 4 else "").strip()
        if not producto or not proveedor or not fecha_raw:
            continue
        cant_raw = str(row[5] if len(row) > 5 else "0").strip()
        try:
            cant = float(cant_raw.replace(",",".")) if cant_raw and cant_raw != "-" else 0.0
        except ValueError:
            cant = 0.0
        conn.execute(
            "INSERT INTO _prov_psp "
            "(producto,proveedor,grupo,etp,fecha,cant_recibida,unidad,"
            "cant_observada,motivo,tiene_remito,control_visual,tiene_informe,"
            "partida,remito,observaciones,archivo_informe,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (producto,
             proveedor,
             str(row[2] if len(row) > 2 else ""),
             str(row[3] if len(row) > 3 else ""),
             to_iso(fecha_raw),
             cant,
             str(row[6]  if len(row) > 6  else "Kg"),
             str(row[7]  if len(row) > 7  else "-"),
             str(row[8]  if len(row) > 8  else "-"),
             si_no(row[9]  if len(row) > 9  else "Si"),
             si_no(row[10] if len(row) > 10 else "Si"),
             si_no(row[11] if len(row) > 11 else "No"),
             str(row[12] if len(row) > 12 else ""),
             str(row[13] if len(row) > 13 else ""),
             str(row[14] if len(row) > 14 else ""),
             "",   # archivo_informe: no se migran los archivos
             now_iso)
        )
        n_psp += 1
    print(f"  PSP:        {n_psp}")

    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    conn.close()
    print("\nMigración completada OK.")


if __name__ == "__main__":
    main()
