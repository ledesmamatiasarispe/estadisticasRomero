"""sync.py — Importa CSVs exportados desde Access a SQLite."""
import csv
import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

EXPORTS_DIR = Path(__file__).parent / "data" / "exports"
DB_PATH     = Path(__file__).parent / "data" / "gnc.db"

# Tablas que soportan sync incremental.
# Clave = nombre exacto de la tabla en Access/SQLite.
# Valor = nombre de la columna PK (auto-increment numérico en Access).
# Solo incluir tablas de tipo append-only (históricos); las que pueden
# tener updates en registros viejos (Pedidos, Remitos activos) quedan fuera.
INCREMENTAL_TABLES: dict[str, str] = {
    "Trabajos":           "iditemtrabajo",    # 83K filas, crece diariamente
    "ItemDetallePedido":  "iditempedido",     # 52K filas, ítems de pedido
    "ItemDetalle":        "iditemdetalle",    # 86K filas, detalles de entrega
    "ItemDevolución":     "iddevol",          # 5K filas, devoluciones externas
    "PreciosHistóricos":  "idpreciohistorico",# 50K filas, historial de precios
    "Impresiones":        "id",               # 21K filas, registros de impresión
    "Pedidos":            "idpedido",         # 21K filas, pedidos de clientes
}


def _sanitize_name(name: str) -> str:
    """Convierte un nombre de tabla/columna Access en un identificador SQLite válido."""
    import re
    s = name.strip()
    s = re.sub(r'[^\w]', '_', s, flags=re.UNICODE)
    if s and s[0].isdigit():
        s = "t_" + s
    return s.lower()


def _infer_type(values: list[str]) -> str:
    """Infiere el tipo SQLite más apropiado para una columna a partir de sus valores."""
    non_empty = [v for v in values if v.strip()]
    if not non_empty:
        return "TEXT"

    def is_int(v):
        try:
            int(v)
            return True
        except ValueError:
            return False

    def is_real(v):
        try:
            float(v.replace(",", "."))
            return True
        except ValueError:
            return False

    if all(is_int(v) for v in non_empty):
        return "INTEGER"
    if all(is_real(v) for v in non_empty):
        return "REAL"
    return "TEXT"


def _coerce(value: str, col_type: str):
    if not value.strip():
        return None
    if col_type == "INTEGER":
        try:
            return int(value)
        except ValueError:
            return None
    if col_type == "REAL":
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None
    return value


def build_watermarks(conn: sqlite3.Connection) -> dict:
    """
    Lee el MAX(pk) de cada tabla incremental que ya existe en SQLite.
    Devuelve dict: {access_table_name: {pk_col, max_val}}.
    """
    watermarks = {}
    for table, pk_col in INCREMENTAL_TABLES.items():
        try:
            row = conn.execute(
                f'SELECT MAX("{pk_col}") FROM "{table}"'
            ).fetchone()
            max_val = row[0] if (row and row[0] is not None) else 0
            watermarks[table] = {"pk_col": pk_col, "max_val": int(max_val)}
            log.debug(f"  watermark {table}.{pk_col} = {max_val}")
        except Exception:
            pass  # tabla no existe todavía → no hay watermark
    return watermarks


def write_watermarks(conn: sqlite3.Connection, exports_dir: Path) -> dict:
    """Escribe watermarks.json en exports_dir y lo devuelve."""
    wm = build_watermarks(conn)
    out = exports_dir / "watermarks.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(wm, f, ensure_ascii=False, indent=2)
    log.info(f"Watermarks escritos para {len(wm)} tablas.")
    return wm


def _existing_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    """Devuelve los nombres de columna del table_info de SQLite."""
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return [r[1] for r in rows]


def import_csv(
    conn: sqlite3.Connection,
    csv_path: Path,
    display_name: str,
    watermark: dict | None = None,
) -> tuple[int, bool]:
    """
    Importa un CSV a SQLite.
    Si watermark es None → sync completo (DROP + CREATE + INSERT).
    Si watermark tiene max_val > 0 → sync incremental (solo inserta filas nuevas).
    Devuelve (filas_importadas, fue_incremental).
    """
    table_name = csv_path.stem

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
        except StopIteration:
            return 0, False

        rows = list(reader)

    if not headers:
        return 0, False

    # Sanitizar nombres de columna
    safe_headers = [_sanitize_name(h) or f"col_{i}" for i, h in enumerate(headers)]
    seen: dict[str, int] = {}
    deduped: list[str] = []
    for h in safe_headers:
        if h in seen:
            seen[h] += 1
            deduped.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            deduped.append(h)
    safe_headers = deduped

    sample = rows[:200]
    col_types = []
    for i in range(len(headers)):
        vals = [r[i] for r in sample if i < len(r)]
        col_types.append(_infer_type(vals))

    placeholders = ", ".join("?" * len(safe_headers))
    insert_sql = f'INSERT INTO "{table_name}" VALUES ({placeholders})'

    def _build_batch(rows_subset):
        batch = []
        for row in rows_subset:
            padded = row + [""] * (len(safe_headers) - len(row))
            coerced = tuple(_coerce(padded[i], col_types[i]) for i in range(len(safe_headers)))
            batch.append(coerced)
        return batch

    # ── Modo incremental ───────────────────────────────────────────────────────
    is_incremental = (
        watermark is not None
        and watermark.get("max_val", 0) > 0
    )

    if is_incremental:
        # Verificar que el esquema coincide (mismas columnas en el mismo orden).
        # Si difiere, caer en sync completo para no romper la tabla.
        existing_cols = _existing_columns(conn, table_name)
        if existing_cols and existing_cols != safe_headers:
            log.warning(
                f"  {display_name}: esquema cambió ({len(existing_cols)} → {len(safe_headers)} cols), "
                "forzando sync completo."
            )
            is_incremental = False

    if is_incremental and not rows:
        # Delta vacío: no hay nada nuevo
        log.info(f"  {display_name}: sin filas nuevas (incremental).")
        return 0, True

    if is_incremental:
        pk_col = watermark["pk_col"]
        max_val = watermark["max_val"]
        # Limpiar cualquier importación parcial previa del mismo rango
        conn.execute(f'DELETE FROM "{table_name}" WHERE "{pk_col}" > ?', (max_val,))
        # Insertar delta
        batch = _build_batch(rows)
        for i in range(0, len(batch), 500):
            conn.executemany(insert_sql, batch[i:i+500])
        log.info(f"  {display_name}: +{len(rows)} filas (incremental, pk>{max_val}).")
        return len(rows), True

    # ── Sync completo ──────────────────────────────────────────────────────────
    col_defs = ", ".join(f'"{h}" {t}' for h, t in zip(safe_headers, col_types))
    conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    conn.execute(f'CREATE TABLE "{table_name}" ({col_defs})')

    conn.execute("""
        INSERT OR REPLACE INTO _table_names (safe_name, display_name, columns_json)
        VALUES (?, ?, ?)
    """, (table_name, display_name, json.dumps(list(zip(safe_headers, headers)))))

    batch = _build_batch(rows)
    for i in range(0, len(batch), 500):
        conn.executemany(insert_sql, batch[i:i+500])

    return len(rows), False


def sync_all(exports_dir: Path = EXPORTS_DIR, db_path: Path = DB_PATH, on_progress=None) -> dict:
    """
    Lee tables.json + todos los CSVs en exports_dir e importa a SQLite.
    Si existe watermarks.json, aplica sync incremental para las tablas configuradas.
    Devuelve un dict con el resultado del sync.
    """
    tables_json = exports_dir / "tables.json"
    if not tables_json.exists():
        raise FileNotFoundError(f"No se encontro {tables_json}. El script de PowerShell falló.")

    with open(tables_json, encoding="utf-8") as f:
        table_meta = json.load(f)

    if isinstance(table_meta, dict):
        table_meta = [table_meta]

    # Leer watermarks si están disponibles
    watermarks: dict = {}
    wm_path = exports_dir / "watermarks.json"
    if wm_path.exists():
        with open(wm_path, encoding="utf-8") as f:
            watermarks = json.load(f)
        log.info(f"Watermarks cargados: {len(watermarks)} tablas en modo incremental.")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS _table_names (
            safe_name    TEXT PRIMARY KEY,
            display_name TEXT,
            columns_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _sync_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT,
            ended_at   TEXT,
            tables_ok  INTEGER,
            tables_err INTEGER,
            detail_json TEXT
        )
    """)

    started = datetime.now().isoformat()
    results = []

    for meta in table_meta:
        if not meta.get("ok", True):
            results.append({"table": meta["table"], "rows": 0, "ok": False, "error": meta.get("error", "")})
            if on_progress:
                on_progress(len(results), len(table_meta))
            continue

        safe_name = meta.get("safe", "")
        display   = meta.get("table", safe_name)
        csv_path  = exports_dir / f"{safe_name}.csv"

        if not csv_path.exists():
            log.warning(f"CSV no encontrado: {csv_path}")
            results.append({"table": display, "rows": 0, "ok": False, "error": "CSV no encontrado"})
            if on_progress:
                on_progress(len(results), len(table_meta))
            continue

        # Usar watermark si el PS marcó esta tabla como incremental Y tenemos el watermark
        wm = watermarks.get(display) if meta.get("incremental") else None

        try:
            rows, incremental = import_csv(conn, csv_path, display, watermark=wm)
            results.append({
                "table":       display,
                "safe":        safe_name,
                "rows":        rows,
                "ok":          True,
                "incremental": incremental,
            })
            log.info(f"  {display}: {rows} filas {'(+delta)' if incremental else '(completo)'}")
        except Exception as e:
            log.error(f"  ERROR {display}: {e}")
            results.append({"table": display, "rows": 0, "ok": False, "error": str(e)})

        if on_progress:
            on_progress(len(results), len(table_meta))

    conn.commit()

    ended = datetime.now().isoformat()
    ok_count  = sum(1 for r in results if r["ok"])
    err_count = len(results) - ok_count

    conn.execute("""
        INSERT INTO _sync_log (started_at, ended_at, tables_ok, tables_err, detail_json)
        VALUES (?, ?, ?, ?, ?)
    """, (started, ended, ok_count, err_count, json.dumps(results)))
    conn.commit()
    conn.close()

    incremental_count = sum(1 for r in results if r.get("incremental"))
    log.info(
        f"Sync completado: {ok_count}/{len(results)} tablas OK "
        f"({incremental_count} incrementales, {ok_count - incremental_count} completas)"
    )
    return {
        "started_at":       started,
        "ended_at":         ended,
        "tables_ok":        ok_count,
        "tables_err":       err_count,
        "tables":           results,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = sync_all()
    print(f"\nOK: {result['tables_ok']} tablas | Errores: {result['tables_err']}")
