"""sync.py — Importa CSVs exportados desde Access a SQLite."""
import csv
import json
import sqlite3
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

EXPORTS_DIR = Path(__file__).parent / "data" / "exports"
DB_PATH     = Path(__file__).parent / "data" / "gnc.db"

# Tablas que soportan sync incremental (solo append-only: nunca se actualizan filas viejas).
INCREMENTAL_TABLES: dict[str, str] = {
    "ItemDetallePedido":  "iditempedido",     # 52K filas, ítems de pedido
    "ItemDetalle":        "iditemdetalle",    # 86K filas, detalles de entrega
    "ItemDevolución":     "iddevol",          # 5K filas, devoluciones externas
    "PreciosHistóricos":  "idpreciohistorico",# 50K filas, historial de precios
    "Impresiones":        "id",               # 21K filas, registros de impresión
    "Pedidos":            "idpedido",         # 21K filas, pedidos de clientes
}

# Tablas que tienen updates en registros existentes (cantidadaprobada, etc.).
# Se re-sincronizan las últimas ROLLING_WINDOW filas con UPSERT en cada ciclo,
# para capturar tanto OTs nuevas como aprobaciones sobre OTs ya existentes.
ROLLING_TABLES: dict[str, str] = {
    "Trabajos": "iditemtrabajo",
}
ROLLING_WINDOW = 600   # re-sync últimas 600 OTs (~6 meses de producción)


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
    Lee el MAX(pk) de cada tabla incremental/rolling que ya existe en SQLite.
    Para ROLLING_TABLES escribe max_val - ROLLING_WINDOW para que Access
    re-exporte ese rango y las aprobaciones tardías queden cubiertas.
    Devuelve dict: {access_table_name: {pk_col, max_val}}.
    """
    watermarks = {}
    all_tables = {**INCREMENTAL_TABLES, **ROLLING_TABLES}
    for table, pk_col in all_tables.items():
        try:
            row = conn.execute(
                f'SELECT MAX("{pk_col}") FROM "{table}"'
            ).fetchone()
            max_val = row[0] if (row and row[0] is not None) else 0
            if table in ROLLING_TABLES:
                effective = max(0, int(max_val) - ROLLING_WINDOW)
            else:
                effective = int(max_val)
            watermarks[table] = {"pk_col": pk_col, "max_val": effective}
            log.debug(f"  watermark {table}.{pk_col} = {effective}")
        except Exception:
            pass
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


BATCH_SIZE = 5000   # filas por executemany (antes 500)


def _parse_csv_file(csv_path: Path, watermark: dict | None, existing_cols: list[str]) -> dict:
    """
    Lee y parsea un CSV completamente en memoria (thread-safe, sin tocar SQLite).
    Devuelve un dict con todo lo necesario para la escritura posterior.
    """
    table_name = csv_path.stem

    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            try:
                headers = next(reader)
            except StopIteration:
                return {"table_name": table_name, "error": "CSV vacío"}
            rows = list(reader)
    except Exception as e:
        return {"table_name": table_name, "error": str(e)}

    if not headers:
        return {"table_name": table_name, "error": "Sin cabeceras"}

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
    col_types = [_infer_type([r[i] for r in sample if i < len(r)]) for i in range(len(headers))]

    # Determinar modo
    is_incremental = watermark is not None and watermark.get("max_val", 0) > 0
    if is_incremental and existing_cols and existing_cols != safe_headers:
        is_incremental = False   # esquema cambió → sync completo

    if is_incremental and not rows:
        return {"table_name": table_name, "mode": "incremental_empty",
                "headers": headers, "safe_headers": safe_headers,
                "col_types": col_types, "batch": [], "watermark": watermark}

    # Convertir todas las filas en memoria
    n = len(safe_headers)
    batch = []
    for row in rows:
        padded = row + [""] * (n - len(row))
        batch.append(tuple(_coerce(padded[i], col_types[i]) for i in range(n)))

    mode = "incremental" if is_incremental else "full"
    return {
        "table_name":  table_name,
        "headers":     headers,
        "safe_headers": safe_headers,
        "col_types":   col_types,
        "batch":       batch,
        "watermark":   watermark,
        "mode":        mode,
    }


def _write_parsed(conn: sqlite3.Connection, parsed: dict, display_name: str) -> tuple[int, bool]:
    """Escribe a SQLite los datos ya parseados. Debe correr en el hilo principal."""
    if "error" in parsed:
        raise RuntimeError(parsed["error"])

    table_name   = parsed["table_name"]
    safe_headers = parsed["safe_headers"]
    headers      = parsed["headers"]
    col_types    = parsed["col_types"]
    batch        = parsed["batch"]
    watermark    = parsed["watermark"]
    mode         = parsed.get("mode", "full")

    placeholders = ", ".join("?" * len(safe_headers))
    insert_sql   = f'INSERT INTO "{table_name}" VALUES ({placeholders})'
    upsert_sql   = f'INSERT OR REPLACE INTO "{table_name}" VALUES ({placeholders})'

    if mode == "incremental_empty":
        log.info(f"  {display_name}: sin filas nuevas (incremental).")
        return 0, True

    if mode == "incremental":
        pk_col  = watermark["pk_col"]
        max_val = watermark["max_val"]
        is_rolling = table_name in ROLLING_TABLES
        if is_rolling:
            conn.execute(f'DELETE FROM "{table_name}" WHERE "{pk_col}" >= ?', (max_val,))
            sql = upsert_sql
            mode_label = f"rolling upsert, pk>={max_val}"
        else:
            conn.execute(f'DELETE FROM "{table_name}" WHERE "{pk_col}" > ?', (max_val,))
            sql = insert_sql
            mode_label = f"incremental, pk>{max_val}"
        for i in range(0, len(batch), BATCH_SIZE):
            conn.executemany(sql, batch[i:i+BATCH_SIZE])
        log.info(f"  {display_name}: +{len(batch)} filas ({mode_label}).")
        return len(batch), True

    # Sync completo
    col_defs = ", ".join(f'"{h}" {t}' for h, t in zip(safe_headers, col_types))
    conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    conn.execute(f'CREATE TABLE "{table_name}" ({col_defs})')
    conn.execute("""
        INSERT OR REPLACE INTO _table_names (safe_name, display_name, columns_json)
        VALUES (?, ?, ?)
    """, (table_name, display_name, json.dumps(list(zip(safe_headers, headers)))))
    for i in range(0, len(batch), BATCH_SIZE):
        conn.executemany(insert_sql, batch[i:i+BATCH_SIZE])
    log.info(f"  {display_name}: {len(batch)} filas (sync completo).")
    return len(batch), False


def import_csv(
    conn: sqlite3.Connection,
    csv_path: Path,
    display_name: str,
    watermark: dict | None = None,
) -> tuple[int, bool]:
    """Wrapper de compatibilidad: parsea + escribe en un solo paso (sin paralelismo)."""
    existing_cols = _existing_columns(conn, csv_path.stem) if watermark else []
    parsed = _parse_csv_file(csv_path, watermark, existing_cols)
    if "error" in parsed:
        return 0, False
    return _write_parsed(conn, parsed, display_name)


def sync_all(exports_dir: Path = EXPORTS_DIR, db_path: Path = DB_PATH,
             on_progress=None, full_refresh: bool = False) -> dict:
    """
    Lee tables.json + todos los CSVs en exports_dir e importa a SQLite.
    Si full_refresh=True ignora watermarks y hace DROP+CREATE+INSERT en todo.
    Si existe watermarks.json, aplica sync incremental/rolling para las tablas configuradas.
    Devuelve un dict con el resultado del sync.
    """
    tables_json = exports_dir / "tables.json"
    if not tables_json.exists():
        raise FileNotFoundError(f"No se encontro {tables_json}. El script de PowerShell falló.")

    with open(tables_json, encoding="utf-8") as f:
        table_meta = json.load(f)

    if isinstance(table_meta, dict):
        table_meta = [table_meta]

    # Leer watermarks si están disponibles (ignorar en full_refresh)
    watermarks: dict = {}
    wm_path = exports_dir / "watermarks.json"
    if not full_refresh and wm_path.exists():
        with open(wm_path, encoding="utf-8") as f:
            watermarks = json.load(f)
        log.info(f"Watermarks cargados: {len(watermarks)} tablas en modo incremental.")
    elif full_refresh:
        log.info("Full refresh: ignorando watermarks, sync completo de todas las tablas.")

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

    # SQLite bulk-import: apagar sync para mayor velocidad (se restaura al final)
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA cache_size = -65536")   # 64 MB de caché

    started = datetime.now().isoformat()
    results = []

    # ── Fase 1: preparar lista de trabajos ────────────────────────────────────
    jobs = []   # list of (meta, csv_path, wm, existing_cols)
    skipped = []
    for meta in table_meta:
        if not meta.get("ok", True):
            skipped.append({"table": meta["table"], "rows": 0, "ok": False,
                            "error": meta.get("error", "")})
            continue
        safe_name = meta.get("safe", "")
        display   = meta.get("table", safe_name)
        csv_path  = exports_dir / f"{safe_name}.csv"
        if not csv_path.exists():
            log.warning(f"CSV no encontrado: {csv_path}")
            skipped.append({"table": display, "rows": 0, "ok": False,
                            "error": "CSV no encontrado"})
            continue
        is_rolling_table = display in ROLLING_TABLES
        wm = (watermarks.get(display)
              if (not full_refresh and (meta.get("incremental") or is_rolling_table))
              else None)
        existing_cols = _existing_columns(conn, safe_name) if wm else []
        jobs.append((meta, csv_path, wm, existing_cols, display, safe_name))

    total = len(jobs) + len(skipped)

    # ── Fase 2: parsear CSVs en paralelo (lectura I/O — sin tocar SQLite) ────
    PARSE_WORKERS = 6
    parsed_map: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=PARSE_WORKERS) as pool:
        future_to_key = {
            pool.submit(_parse_csv_file, csv_path, wm, existing_cols): display
            for (meta, csv_path, wm, existing_cols, display, safe_name) in jobs
        }
        for future in as_completed(future_to_key):
            display = future_to_key[future]
            try:
                parsed_map[display] = future.result()
            except Exception as e:
                parsed_map[display] = {"table_name": display, "error": str(e)}

    # ── Fase 3: escribir a SQLite en el orden original (un solo escritor) ────
    for meta, csv_path, wm, existing_cols, display, safe_name in jobs:
        parsed = parsed_map.get(display, {"table_name": display, "error": "no parseado"})
        try:
            n_rows, incremental = _write_parsed(conn, parsed, display)
            results.append({
                "table":       display,
                "safe":        safe_name,
                "rows":        n_rows,
                "ok":          True,
                "incremental": incremental,
            })
        except Exception as e:
            log.error(f"  ERROR {display}: {e}")
            results.append({"table": display, "rows": 0, "ok": False, "error": str(e)})

        if on_progress:
            on_progress(len(results) + len(skipped), total)

    results = skipped + results
    conn.commit()
    conn.execute("PRAGMA synchronous = NORMAL")   # restaurar

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
