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

# Tablas append-only: solo se insertan filas nuevas (pk > watermark).
INCREMENTAL_TABLES: dict[str, str] = {
    "ItemDetallePedido":  "iditempedido",      # 52K filas
    "ItemDetalle":        "iditemdetalle",      # 86K filas
    "ItemDevolución":     "iddevol",            # 5K filas
    "PreciosHistóricos":  "idpreciohistorico",  # 50K filas
    "Impresiones":        "id",                 # 21K filas
    "Pedidos":            "idpedido",           # 21K filas
    "Remitos":            "idnroremito",        # append-only, los remitos no se editan
}

# Tablas exportadas con filtro de fecha.
# El PS exporta los N meses recientes; SQLite hace DELETE-WHERE-PK-IN + INSERT.
# Las tablas en SQLite NO tienen PK declarado (esquema plano), por eso no podemos
# usar INSERT OR REPLACE — en cambio borramos los IDs del batch y re-insertamos.
# Valor: nombre safe (lowercase) de la columna PK en SQLite.
DATE_ROLLING_TABLES: dict[str, str] = {
    "Trabajos": "iditemtrabajo",
}

# Rolling por conteo de IDs (ya no se usa — Trabajos pasó a date-rolling).
ROLLING_TABLES: dict[str, str] = {}
ROLLING_WINDOW = 600  # mantenido por si se agrega alguna tabla en el futuro

# Tablas gestionadas manualmente: el sync nunca las sobreescribe.
FROZEN_TABLES: set[str] = {
    "TiposMaterial", "TiposDeMatriz", "TiposDeModelos", "TiposDeMoldeo",
    "TiposDeCarga", "TiposMorfologíaGrafito", "TiposParteDeModelo",
    "TiposDocumento", "TiposDocumentoExterno", "TiposDomicilio",
    "TiposFacturación", "TiposModelo", "TipoCondicionPago",
    "DefectosInternos", "Sectores", "Estados",
}

# Grupos para sync rotativo. Cada grupo se actualiza de forma independiente.
SYNC_GROUPS: dict[str, list[str]] = {
    "en_curso": [
        "Trabajos",           # date-rolling: últimos 3 meses, UPSERT
        "HorasPorFecha",      # full (pequeña, ~50 registros/día)
        "FundiciónPorFecha",  # full (pequeña, clave compuesta)
        "Fundiciones",        # full (pequeña)
    ],
    "movimientos": [
        "Pedidos",            # incremental por idpedido
        "ItemDetallePedido",  # incremental por iditempedido
        "ItemDetalle",        # incremental por iditemdetalle
        "Remitos",            # incremental por idnroremito
        "ItemDevolución",     # incremental por iddevol
    ],
    "resoluciones": [
        "SoluciónRechazoInterno",     # full (pequeña, vinculada a Trabajos)
        "SoluciónReparacionInterna",  # full (pequeña)
        "SoluciónDevolución",         # full (pequeña)
        "Impresiones",                # incremental por id
    ],
    "maestros": [
        "Clientes", "NombreDePiezas", "PesosDePiezas",
        "Modelos", "PiezasPorModelo", "Responsables",
        "RendimientoMetal", "FechasFundición", "PreciosHistóricos",
    ],
}

# Intervalo mínimo entre syncs de cada grupo (segundos).
GROUP_INTERVALS: dict[str, int] = {
    "en_curso":    15 * 60,    #  15 minutos
    "movimientos": 30 * 60,    #  30 minutos
    "resoluciones":30 * 60,    #  30 minutos
    "maestros":    30 * 60,    #  30 minutos
}


# ── Helpers internos ──────────────────────────────────────────────────────────

def _sanitize_name(name: str) -> str:
    import re
    s = name.strip()
    s = re.sub(r'[^\w]', '_', s, flags=re.UNICODE)
    if s and s[0].isdigit():
        s = "t_" + s
    return s.lower()


def _infer_type(values: list[str]) -> str:
    non_empty = [v for v in values if v.strip()]
    if not non_empty:
        return "TEXT"

    def is_int(v):
        try: int(v); return True
        except ValueError: return False

    def is_real(v):
        try: float(v.replace(",", ".")); return True
        except ValueError: return False

    if all(is_int(v) for v in non_empty):   return "INTEGER"
    if all(is_real(v) for v in non_empty):  return "REAL"
    return "TEXT"


def _coerce(value: str, col_type: str):
    if not value.strip():
        return None
    if col_type == "INTEGER":
        try: return int(value)
        except ValueError: return None
    if col_type == "REAL":
        try: return float(value.replace(",", "."))
        except ValueError: return None
    return value


# ── Watermarks ────────────────────────────────────────────────────────────────

def build_watermarks(conn: sqlite3.Connection, table_filter: set[str] | None = None) -> dict:
    """
    Lee MAX(pk) de tablas incrementales/rolling que ya existen en SQLite.
    Excluye DATE_ROLLING_TABLES (no necesitan watermark de ID).
    Si table_filter es set, solo construye watermarks para esas tablas.
    """
    watermarks = {}
    candidates = {**INCREMENTAL_TABLES, **ROLLING_TABLES}
    for table, pk_col in candidates.items():
        if table in DATE_ROLLING_TABLES:
            continue
        if table_filter is not None and table not in table_filter:
            continue
        try:
            row = conn.execute(f'SELECT MAX("{pk_col}") FROM "{table}"').fetchone()
            max_val = row[0] if (row and row[0] is not None) else 0
            effective = max(0, int(max_val) - ROLLING_WINDOW) if table in ROLLING_TABLES else int(max_val)
            watermarks[table] = {"pk_col": pk_col, "max_val": effective}
            log.debug("  watermark %s.%s = %d", table, pk_col, effective)
        except Exception:
            pass
    return watermarks


def write_watermarks(conn: sqlite3.Connection, exports_dir: Path) -> dict:
    """Escribe watermarks.json con todas las tablas (sync completo)."""
    wm = build_watermarks(conn)
    out = exports_dir / "watermarks.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(wm, f, ensure_ascii=False, indent=2)
    log.info("Watermarks escritos para %d tablas.", len(wm))
    return wm


def write_group_watermarks(conn: sqlite3.Connection, exports_dir: Path, group_name: str) -> dict:
    """
    Actualiza watermarks.json solo para las tablas incrementales del grupo.
    Preserva los watermarks de los otros grupos (merge, no reemplaza).
    """
    group_tables = set(SYNC_GROUPS.get(group_name, []))
    new_wm = build_watermarks(conn, table_filter=group_tables)

    wm_path = exports_dir / "watermarks.json"
    existing: dict = {}
    if wm_path.exists():
        try:
            with open(wm_path, encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass

    existing.update(new_wm)
    with open(wm_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    log.info("Watermarks del grupo '%s' actualizados: %d tablas.", group_name, len(new_wm))
    return existing


# ── Parsing / importación ─────────────────────────────────────────────────────

def _existing_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return [r[1] for r in rows]


BATCH_SIZE = 5000


def _parse_csv_file(
    csv_path: Path,
    watermark: dict | None,
    existing_cols: list[str],
    mode_override: str | None = None,
) -> dict:
    """
    Lee y parsea un CSV completamente en memoria (thread-safe, sin tocar SQLite).
    mode_override: "date_rolling" fuerza ese modo independientemente del watermark.
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

    sample     = rows[:200]
    col_types  = [_infer_type([r[i] for r in sample if i < len(r)]) for i in range(len(headers))]

    # Modo forzado desde afuera (date_rolling)
    if mode_override:
        n = len(safe_headers)
        batch = [tuple(_coerce((row + [""] * (n - len(row)))[i], col_types[i]) for i in range(n))
                 for row in rows]
        return {
            "table_name": table_name, "headers": headers,
            "safe_headers": safe_headers, "col_types": col_types,
            "batch": batch, "watermark": None, "mode": mode_override,
        }

    is_incremental = watermark is not None and watermark.get("max_val", 0) > 0
    if is_incremental and existing_cols and existing_cols != safe_headers:
        is_incremental = False  # esquema cambió → sync completo

    if is_incremental and not rows:
        return {"table_name": table_name, "mode": "incremental_empty",
                "headers": headers, "safe_headers": safe_headers,
                "col_types": col_types, "batch": [], "watermark": watermark}

    n = len(safe_headers)
    batch = [tuple(_coerce((row + [""] * (n - len(row)))[i], col_types[i]) for i in range(n))
             for row in rows]

    return {
        "table_name": table_name, "headers": headers,
        "safe_headers": safe_headers, "col_types": col_types,
        "batch": batch, "watermark": watermark,
        "mode": "incremental" if is_incremental else "full",
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

    # ── date_rolling: DELETE pk IN (...) + INSERT ────────────────────────────
    # Las tablas no tienen PK declarado, así que INSERT OR REPLACE sería solo INSERT.
    # En cambio borramos todos los IDs que están en el batch y re-insertamos.
    if mode == "date_rolling":
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
        ).fetchone()
        if not exists:
            col_defs = ", ".join(f'"{h}" {t}' for h, t in zip(safe_headers, col_types))
            conn.execute(f'CREATE TABLE "{table_name}" ({col_defs})')
            conn.execute(
                "INSERT OR REPLACE INTO _table_names (safe_name, display_name, columns_json) VALUES (?,?,?)",
                (table_name, display_name, json.dumps(list(zip(safe_headers, headers))))
            )
        pk_col = DATE_ROLLING_TABLES.get(table_name, "")
        if pk_col and pk_col in safe_headers:
            pk_idx  = safe_headers.index(pk_col)
            all_ids = [row[pk_idx] for row in batch if row[pk_idx] is not None]
            # DELETE en lotes de 500 (límite de variables de SQLite)
            for j in range(0, len(all_ids), 500):
                sub = all_ids[j:j+500]
                conn.execute(
                    f'DELETE FROM "{table_name}" WHERE "{pk_col}" IN ({",".join("?"*len(sub))})', sub
                )
        for i in range(0, len(batch), BATCH_SIZE):
            conn.executemany(insert_sql, batch[i:i+BATCH_SIZE])
        log.info("  %s: %d filas (date-rolling, delete+insert).", display_name, len(batch))
        return len(batch), True

    # ── incremental_empty ─────────────────────────────────────────────────────
    if mode == "incremental_empty":
        log.info("  %s: sin filas nuevas (incremental).", display_name)
        return 0, True

    # ── incremental ───────────────────────────────────────────────────────────
    if mode == "incremental":
        pk_col     = watermark["pk_col"]
        max_val    = watermark["max_val"]
        is_rolling = table_name in ROLLING_TABLES
        if is_rolling:
            conn.execute(f'DELETE FROM "{table_name}" WHERE "{pk_col}" >= ?', (max_val,))
            sql        = upsert_sql
            mode_label = f"rolling upsert, pk>={max_val}"
        else:
            conn.execute(f'DELETE FROM "{table_name}" WHERE "{pk_col}" > ?', (max_val,))
            sql        = insert_sql
            mode_label = f"incremental, pk>{max_val}"
        for i in range(0, len(batch), BATCH_SIZE):
            conn.executemany(sql, batch[i:i+BATCH_SIZE])
        log.info("  %s: +%d filas (%s).", display_name, len(batch), mode_label)
        return len(batch), True

    # ── full: DROP + CREATE + INSERT ─────────────────────────────────────────
    col_defs = ", ".join(f'"{h}" {t}' for h, t in zip(safe_headers, col_types))
    conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    conn.execute(f'CREATE TABLE "{table_name}" ({col_defs})')
    conn.execute(
        "INSERT OR REPLACE INTO _table_names (safe_name, display_name, columns_json) VALUES (?,?,?)",
        (table_name, display_name, json.dumps(list(zip(safe_headers, headers))))
    )
    for i in range(0, len(batch), BATCH_SIZE):
        conn.executemany(insert_sql, batch[i:i+BATCH_SIZE])
    log.info("  %s: %d filas (sync completo).", display_name, len(batch))
    return len(batch), False


def import_csv(
    conn: sqlite3.Connection,
    csv_path: Path,
    display_name: str,
    watermark: dict | None = None,
) -> tuple[int, bool]:
    """Wrapper de compatibilidad: parsea + escribe en un solo paso."""
    existing_cols = _existing_columns(conn, csv_path.stem) if watermark else []
    parsed = _parse_csv_file(csv_path, watermark, existing_cols)
    if "error" in parsed:
        return 0, False
    return _write_parsed(conn, parsed, display_name)


# ── sync_all ──────────────────────────────────────────────────────────────────

def sync_all(
    exports_dir: Path = EXPORTS_DIR,
    db_path: Path = DB_PATH,
    on_progress=None,
    full_refresh: bool = False,
    tables: set[str] | None = None,
) -> dict:
    """
    Lee tables.json + CSVs en exports_dir e importa a SQLite.
    tables: si se pasa un set, solo importa las tablas de ese set.
    full_refresh: ignora watermarks y hace DROP+CREATE+INSERT en todo.
    """
    tables_json = exports_dir / "tables.json"
    if not tables_json.exists():
        raise FileNotFoundError(f"No se encontró {tables_json}. El script de PowerShell falló.")

    with open(tables_json, encoding="utf-8") as f:
        table_meta = json.load(f)
    if isinstance(table_meta, dict):
        table_meta = [table_meta]

    watermarks: dict = {}
    wm_path = exports_dir / "watermarks.json"
    if not full_refresh and wm_path.exists():
        with open(wm_path, encoding="utf-8") as f:
            watermarks = json.load(f)
        log.info("Watermarks cargados: %d tablas.", len(watermarks))
    elif full_refresh:
        log.info("Full refresh: sync completo de todas las tablas.")

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
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA cache_size = -65536")

    started = datetime.now().isoformat()
    results = []

    # ── Fase 1: preparar trabajos ─────────────────────────────────────────────
    jobs    = []
    skipped = []
    for meta in table_meta:
        if not meta.get("ok", True):
            skipped.append({"table": meta["table"], "rows": 0, "ok": False,
                            "error": meta.get("error", "")})
            continue
        safe_name    = meta.get("safe", "")
        display      = meta.get("table", safe_name)
        csv_path     = exports_dir / f"{safe_name}.csv"

        # Filtro de grupo
        if tables is not None and display not in tables:
            continue

        if display in FROZEN_TABLES:
            skipped.append({"table": display, "rows": 0, "ok": True,
                            "error": "tabla congelada (sin sync)"})
            continue
        if not csv_path.exists():
            log.warning("CSV no encontrado: %s", csv_path)
            skipped.append({"table": display, "rows": 0, "ok": False,
                            "error": "CSV no encontrado"})
            continue

        # Determinar mode_override
        mode_override = None
        if not full_refresh and meta.get("date_rolling") and display in DATE_ROLLING_TABLES:
            mode_override = "date_rolling"

        is_rolling  = display in ROLLING_TABLES
        is_incr     = display in INCREMENTAL_TABLES
        wm = (watermarks.get(display)
              if (not full_refresh and (meta.get("incremental") or is_rolling or is_incr)
                  and mode_override is None)
              else None)
        existing_cols = _existing_columns(conn, safe_name) if wm else []
        jobs.append((meta, csv_path, wm, existing_cols, display, safe_name, mode_override))

    total = len(jobs) + len(skipped)

    # ── Fase 2: parsear en paralelo ───────────────────────────────────────────
    PARSE_WORKERS = 6
    parsed_map: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=PARSE_WORKERS) as pool:
        future_to_key = {
            pool.submit(_parse_csv_file, csv_path, wm, existing_cols, mode_override): display
            for (meta, csv_path, wm, existing_cols, display, safe_name, mode_override) in jobs
        }
        for future in as_completed(future_to_key):
            display = future_to_key[future]
            try:
                parsed_map[display] = future.result()
            except Exception as e:
                parsed_map[display] = {"table_name": display, "error": str(e)}

    # ── Fase 3: escribir a SQLite ─────────────────────────────────────────────
    for meta, csv_path, wm, existing_cols, display, safe_name, mode_override in jobs:
        parsed = parsed_map.get(display, {"table_name": display, "error": "no parseado"})
        try:
            n_rows, incremental = _write_parsed(conn, parsed, display)
            results.append({"table": display, "safe": safe_name,
                            "rows": n_rows, "ok": True, "incremental": incremental})
        except Exception as e:
            log.error("  ERROR %s: %s", display, e)
            results.append({"table": display, "rows": 0, "ok": False, "error": str(e)})
        if on_progress:
            on_progress(len(results) + len(skipped), total)

    results = skipped + results
    conn.commit()
    conn.execute("PRAGMA synchronous = NORMAL")

    ended     = datetime.now().isoformat()
    ok_count  = sum(1 for r in results if r["ok"])
    err_count = len(results) - ok_count

    conn.execute(
        "INSERT INTO _sync_log (started_at, ended_at, tables_ok, tables_err, detail_json) VALUES (?,?,?,?,?)",
        (started, ended, ok_count, err_count, json.dumps(results))
    )
    conn.commit()
    conn.close()

    incr_count = sum(1 for r in results if r.get("incremental"))
    log.info("Sync completado: %d/%d tablas OK (%d incrementales, %d completas)",
             ok_count, len(results), incr_count, ok_count - incr_count)
    return {"started_at": started, "ended_at": ended,
            "tables_ok": ok_count, "tables_err": err_count, "tables": results}


def sync_group(
    group_name: str,
    exports_dir: Path = EXPORTS_DIR,
    db_path: Path = DB_PATH,
    on_progress=None,
) -> dict:
    """Sincroniza solo las tablas del grupo especificado."""
    group_tables = set(SYNC_GROUPS.get(group_name, []))
    if not group_tables:
        raise ValueError(f"Grupo desconocido: '{group_name}'")
    return sync_all(exports_dir, db_path, on_progress=on_progress, tables=group_tables)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = sync_all()
    print(f"\nOK: {result['tables_ok']} tablas | Errores: {result['tables_err']}")
