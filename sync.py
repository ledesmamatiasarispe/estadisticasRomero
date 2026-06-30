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

    # Intentar INTEGER
    def is_int(v):
        try:
            int(v)
            return True
        except ValueError:
            return False

    # Intentar REAL
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


def import_csv(conn: sqlite3.Connection, csv_path: Path, display_name: str) -> int:
    """Importa un CSV a SQLite. Devuelve cantidad de filas insertadas."""
    table_name = csv_path.stem  # ya viene sanitizado del PS script

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
        except StopIteration:
            return 0

        rows = list(reader)

    if not headers:
        return 0

    # Sanitizar nombres de columna
    safe_headers = [_sanitize_name(h) or f"col_{i}" for i, h in enumerate(headers)]
    # Desduplicar si hay colisiones
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

    # Inferir tipos usando primeras 200 filas
    sample = rows[:200]
    col_types = []
    for i in range(len(headers)):
        vals = [r[i] for r in sample if i < len(r)]
        col_types.append(_infer_type(vals))

    # Crear tabla (DROP + CREATE para refresh completo)
    col_defs = ", ".join(
        f'"{h}" {t}' for h, t in zip(safe_headers, col_types)
    )
    conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    conn.execute(f'CREATE TABLE "{table_name}" ({col_defs})')

    # Guardar mapeo nombre_original → nombre_sqlite y columnas originales
    conn.execute("""
        INSERT OR REPLACE INTO _table_names (safe_name, display_name, columns_json)
        VALUES (?, ?, ?)
    """, (table_name, display_name, json.dumps(list(zip(safe_headers, headers)))))

    # Insertar filas en lotes
    placeholders = ", ".join("?" * len(safe_headers))
    insert_sql = f'INSERT INTO "{table_name}" VALUES ({placeholders})'

    batch = []
    for row in rows:
        # Rellenar con None si la fila tiene menos columnas
        padded = row + [""] * (len(safe_headers) - len(row))
        coerced = tuple(_coerce(padded[i], col_types[i]) for i in range(len(safe_headers)))
        batch.append(coerced)
        if len(batch) >= 500:
            conn.executemany(insert_sql, batch)
            batch.clear()
    if batch:
        conn.executemany(insert_sql, batch)

    return len(rows)


def sync_all(exports_dir: Path = EXPORTS_DIR, db_path: Path = DB_PATH, on_progress=None) -> dict:
    """
    Lee tables.json + todos los CSVs en exports_dir e importa a SQLite.
    Devuelve un dict con el resultado del sync.
    """
    tables_json = exports_dir / "tables.json"
    if not tables_json.exists():
        raise FileNotFoundError(f"No se encontro {tables_json}. El script de PowerShell falló.")

    with open(tables_json, encoding="utf-8") as f:
        table_meta = json.load(f)

    # Si es un solo objeto (tabla única), convertir a lista
    if isinstance(table_meta, dict):
        table_meta = [table_meta]

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    # Tabla de metadatos
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
            continue

        safe_name = meta.get("safe", "")
        display   = meta.get("table", safe_name)
        csv_path  = exports_dir / f"{safe_name}.csv"

        if not csv_path.exists():
            log.warning(f"CSV no encontrado: {csv_path}")
            results.append({"table": display, "rows": 0, "ok": False, "error": "CSV no encontrado"})
            continue

        try:
            rows = import_csv(conn, csv_path, display)
            results.append({"table": display, "safe": safe_name, "rows": rows, "ok": True})
            log.info(f"  {display}: {rows} filas")
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

    log.info(f"Sync completado: {ok_count}/{len(results)} tablas OK")
    return {
        "started_at": started,
        "ended_at":   ended,
        "tables_ok":  ok_count,
        "tables_err": err_count,
        "tables":     results,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = sync_all()
    print(f"\nOK: {result['tables_ok']} tablas | Errores: {result['tables_err']}")
