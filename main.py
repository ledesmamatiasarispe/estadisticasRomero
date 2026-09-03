"""main.py — GNC API: FastAPI + sync Access → SQLite + frontend."""
import asyncio
import concurrent.futures as _cf
import hashlib
import json
import logging
import os
import secrets
import sqlite3
import subprocess
import sys
import time as _time
from contextlib import asynccontextmanager
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

ROOT       = Path(__file__).parent
DB_PATH    = ROOT / "data" / "gnc.db"
EXPORTS    = ROOT / "data" / "exports"
FRONTEND   = ROOT / "frontend"
PS32       = r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
PS_SCRIPT  = ROOT / "sync_access.ps1"

# Carpetas con fotos de modelos, en orden de prioridad -- las fotos de FotosDeModelos
# quedaron repartidas entre estas dos redes compartidas con el tiempo (833 nombres se
# repiten en las dos, pero cada una tiene ~3500 que la otra no tiene), así que hay que
# buscar el archivo en todas antes de darlo por no encontrado.
FOTOS_MODELOS_PATHS: list[str] = [
    r"M:\ArchivosCompartidosResguardo\ArchivosCompartidos NO BORRAR\FotosDigitales\FotosdB\Modelos",
    r"M:\Bases\FotosDigitales",
    r"M:\ArchivosCompartidosResguardo\ArchivosCompartidos NO BORRAR\FotosDigitales\FotosdB\Modelos retirados - Entregados",
]
INFORMES_PSP = ROOT / "data" / "informes_psp"
INFORMES_PSP.mkdir(parents=True, exist_ok=True)

_fotos_ok: Optional[bool] = None
_fotos_ok_ts: float = 0.0

def _fotos_disponibles() -> bool:
    """Check that at least one photo folder is reachable, with 2s timeout; cached 30s."""
    global _fotos_ok, _fotos_ok_ts
    now = _time.monotonic()
    if _fotos_ok is not None and (now - _fotos_ok_ts) < 30:
        return bool(_fotos_ok)
    try:
        with _cf.ThreadPoolExecutor(max_workers=len(FOTOS_MODELOS_PATHS)) as ex:
            futures = [ex.submit(os.path.isdir, p) for p in FOTOS_MODELOS_PATHS]
            result = any(f.result(timeout=2) for f in futures)
        _fotos_ok = bool(result)
    except Exception:
        _fotos_ok = False
    _fotos_ok_ts = _time.monotonic()
    return bool(_fotos_ok)


def _buscar_foto_modelo(filename: str) -> Optional[Path]:
    """Primera carpeta de FOTOS_MODELOS_PATHS que tenga este archivo, o None."""
    for carpeta in FOTOS_MODELOS_PATHS:
        candidato = Path(carpeta) / filename
        try:
            with _cf.ThreadPoolExecutor(max_workers=1) as ex:
                existe = ex.submit(candidato.exists).result(timeout=2)
        except Exception:
            existe = False
        if existe:
            return candidato
    return None

_MESES_ES = ['', 'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun',
             'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']


def _sem_label(fecha_str: str) -> str:
    """'21–27 Jul' or '28 Jul–3 Ago' for the Mon-Sun week containing fecha_str."""
    try:
        d = date_cls.fromisoformat(fecha_str[:10])
        mon = d - timedelta(days=d.weekday())
        sun = mon + timedelta(days=6)
        if mon.month == sun.month:
            return f"{mon.day}–{sun.day} {_MESES_ES[mon.month]}"
        return f"{mon.day} {_MESES_ES[mon.month]}–{sun.day} {_MESES_ES[sun.month]}"
    except Exception:
        return (fecha_str or '')[:10]


def _iso_week_label(año: int, semana: int) -> str:
    """Same range label for an ISO week number (1–53)."""
    try:
        mon = date_cls.fromisocalendar(int(año), int(semana), 1)
        return _sem_label(mon.isoformat())
    except Exception:
        return f"Sem.{int(semana):02d}/{int(año)}"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Estado del sync ────────────────────────────────────────────────────────────

sync_state: dict = {
    "status":    "idle",   # idle | syncing | ready | error
    "last_sync": None,
    "tables":    [],
    "error":     None,
    "progress":  {"done": 0, "total": 0, "phase": ""},
    "groups":    {},       # poblado en lifespan desde sync.SYNC_GROUPS
}

# Timestamps monotónicos de último sync por grupo (clock del event loop).
# Compartido entre run_sync(), run_sync_group() y _group_scheduler().
_last_group_run: dict[str, float] = {}


def _should_auto_sync() -> bool:
    """True si no hay sync previo o el último terminó hace ≥10 horas."""
    if not DB_PATH.exists():
        return True
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT ended_at FROM _sync_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            return True
        last = datetime.fromisoformat(row[0])
        elapsed = (datetime.now() - last).total_seconds()
        return elapsed >= 36000  # 10 hours
    except Exception:
        return True


# ── Sync ───────────────────────────────────────────────────────────────────────

async def run_sync(full_refresh: bool = False):
    import sync as sync_module

    sync_state["status"]   = "syncing"
    sync_state["error"]    = None
    mode_label = "completo" if full_refresh else "incremental"
    sync_state["progress"] = {"done": 0, "total": 0, "phase": f"Exportando desde Access ({mode_label})..."}
    log.info("Iniciando sync Access → SQLite (full_refresh=%s)...", full_refresh)

    loop = asyncio.get_event_loop()

    # 0. Escribir watermarks (omitir en full_refresh para forzar sync completo)
    if not full_refresh and DB_PATH.exists():
        try:
            _wm_conn = sqlite3.connect(DB_PATH)
            wm = sync_module.write_watermarks(_wm_conn, EXPORTS)
            _wm_conn.close()
            log.info("Watermarks escritos: %d tablas en modo incremental.", len(wm))
        except Exception as _e:
            log.warning("No se pudieron escribir watermarks: %s. Sync será completo.", _e)
    elif full_refresh:
        # Borrar watermarks para que Access exporte todo
        wm_path = EXPORTS / "watermarks.json"
        if wm_path.exists():
            wm_path.unlink()
        log.info("Full refresh: watermarks eliminados, Access exportará todas las filas.")

    # 1. PowerShell 32-bit: Access → CSVs
    def _run_ps():
        return subprocess.run(
            [PS32, "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(PS_SCRIPT),
             "-OutputDir", str(EXPORTS),
             "-DbPath", r"M:\bases\2011\datos\datosunificado2010.accdb"],
            capture_output=False,
        )

    result = await loop.run_in_executor(None, _run_ps)
    if result.returncode != 0:
        sync_state["status"]   = "error"
        sync_state["error"]    = "PowerShell sync falló (ver consola)"
        sync_state["progress"] = {"done": 0, "total": 0, "phase": ""}
        log.error("PowerShell sync falló con código %d", result.returncode)
        return

    # 2. CSV → SQLite
    sync_state["progress"] = {"done": 0, "total": 0, "phase": "Importando tablas..."}

    def _on_progress(done: int, total: int):
        sync_state["progress"] = {"done": done, "total": total, "phase": "Importando tablas..."}

    def _run_import():
        return sync_module.sync_all(EXPORTS, DB_PATH,
                                    on_progress=_on_progress,
                                    full_refresh=full_refresh)

    try:
        summary = await loop.run_in_executor(None, _run_import)
        sync_state["status"]    = "ready"
        sync_state["last_sync"] = summary["ended_at"]
        sync_state["progress"]  = {"done": 0, "total": 0, "phase": ""}
        sync_state["tables"]    = [
            {"name": t["table"], "safe": t.get("safe", ""), "rows": t["rows"]}
            for t in summary["tables"] if t["ok"]
        ]
        log.info("Sync completado: %d tablas OK, %d errores",
                 summary["tables_ok"], summary["tables_err"])
    except Exception as e:
        sync_state["status"]   = "error"
        sync_state["error"]    = str(e)
        sync_state["progress"] = {"done": 0, "total": 0, "phase": ""}
        log.error("Error en importación: %s", e)
        return

    # Marcar todos los grupos como recién sincronizados (full sync cubre todo)
    now_mono = asyncio.get_event_loop().time()
    for group in list(sync_state["groups"].keys()):
        _last_group_run[group] = now_mono
        sync_state["groups"][group]["last_sync"] = sync_state["last_sync"]


async def run_sync_group(group_name: str):
    """Sincroniza solo el grupo especificado: PS1 filtrado → sync_group()."""
    import sync as sync_module

    group_tables = sync_module.SYNC_GROUPS.get(group_name, [])
    if not group_tables:
        log.warning("run_sync_group: grupo desconocido '%s'", group_name)
        return
    if sync_state["status"] == "syncing":
        log.debug("run_sync_group: sync en progreso, saltando grupo '%s'", group_name)
        return

    sync_state["status"]   = "syncing"
    sync_state["error"]    = None
    sync_state["progress"] = {"done": 0, "total": 0, "phase": f"Exportando grupo '{group_name}'..."}
    log.info("Sync grupo '%s': %d tablas", group_name, len(group_tables))

    loop = asyncio.get_event_loop()

    # 0. Actualizar watermarks solo para las tablas incrementales del grupo
    if DB_PATH.exists():
        try:
            _wm_conn = sqlite3.connect(DB_PATH)
            sync_module.write_group_watermarks(_wm_conn, EXPORTS, group_name)
            _wm_conn.close()
        except Exception as _e:
            log.warning("No se pudieron escribir watermarks del grupo '%s': %s", group_name, _e)

    # 1. PowerShell: exportar solo las tablas del grupo
    tables_str = ";".join(group_tables)

    def _run_ps_group():
        return subprocess.run(
            [PS32, "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(PS_SCRIPT),
             "-OutputDir", str(EXPORTS),
             "-DbPath", r"M:\bases\2011\datos\datosunificado2010.accdb",
             "-Tables", tables_str],
            capture_output=False,
        )

    result = await loop.run_in_executor(None, _run_ps_group)
    if result.returncode != 0:
        sync_state["status"]   = "error"
        sync_state["error"]    = f"PowerShell falló para grupo '{group_name}'"
        sync_state["progress"] = {"done": 0, "total": 0, "phase": ""}
        log.error("PS1 falló para grupo '%s' (código %d)", group_name, result.returncode)
        return

    # 2. CSV → SQLite (solo tablas del grupo)
    sync_state["progress"] = {"done": 0, "total": 0, "phase": f"Importando grupo '{group_name}'..."}

    def _on_progress(done: int, total: int):
        sync_state["progress"] = {"done": done, "total": total,
                                   "phase": f"Importando '{group_name}'..."}

    def _run_import():
        return sync_module.sync_group(group_name, EXPORTS, DB_PATH, on_progress=_on_progress)

    try:
        summary = await loop.run_in_executor(None, _run_import)
        now_iso  = summary["ended_at"]
        now_mono = loop.time()
        sync_state["status"]    = "ready"
        sync_state["last_sync"] = now_iso
        sync_state["progress"]  = {"done": 0, "total": 0, "phase": ""}
        _last_group_run[group_name] = now_mono
        if group_name in sync_state["groups"]:
            sync_state["groups"][group_name]["last_sync"]  = now_iso
            sync_state["groups"][group_name]["tables_ok"]  = summary["tables_ok"]
        log.info("Sync grupo '%s' OK: %d tablas, %d errores",
                 group_name, summary["tables_ok"], summary["tables_err"])
    except Exception as e:
        sync_state["status"]   = "error"
        sync_state["error"]    = str(e)
        sync_state["progress"] = {"done": 0, "total": 0, "phase": ""}
        log.error("Error en sync grupo '%s': %s", group_name, e)


async def _group_scheduler():
    """Loop asyncio: sincroniza el grupo más vencido cada 60 s (uno por tick)."""
    import sync as sync_module
    log.info("Scheduler de grupos iniciado.")
    while True:
        await asyncio.sleep(60)
        if sync_state["status"] == "syncing":
            continue
        now = asyncio.get_event_loop().time()
        best_group:   str | None = None
        best_overdue: float = 0
        for group, interval in sync_module.GROUP_INTERVALS.items():
            last    = _last_group_run.get(group, 0)
            overdue = (now - last) - interval
            if overdue > 0 and overdue > best_overdue:
                best_overdue = overdue
                best_group   = group
        if best_group:
            log.info("Scheduler: grupo '%s' vencido por %.0fs", best_group, best_overdue)
            await run_sync_group(best_group)


# ── DB helper ─────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(503, "Base de datos no disponible aún — sync en progreso")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA mmap_size = 134217728")   # 128 MB mmap — pages shared via OS file cache
    conn.execute("PRAGMA cache_size = -8000")       # 8 MB SQLite page cache per connection
    conn.execute("PRAGMA temp_store = MEMORY")      # temp B-trees in RAM, not disk
    return conn


# Columnas FK/relación a excluir del raw display por tabla
_FK_COLS: dict[str, set] = {
    "Clientes":       {"códigoproveedor"},
    "Pedidos":        {"códigocliente"},
    "Remitos":        {"códigocliente"},
    "Modelos":        {"códigodueño", "tipomodelo", "idcajamoldeo"},
    "Fundiciones":    {"códrespinformematerial", "códvbinformematerial",
                       "códrespcargarech", "codtipocarga"},
    "NombreDePiezas": {"códcliente", "códdeagregados", "códmaterial", "códespesor"},
}

def _raw(conn, table: str, pk_col: str, pk_val) -> dict:
    """Devuelve SELECT * excluyendo columnas FK de la tabla indicada."""
    row = conn.execute(f'SELECT * FROM "{table}" WHERE "{pk_col}" = ?', (pk_val,)).fetchone()
    if not row:
        return {}
    d = dict(row)
    for col in _FK_COLS.get(table, set()):
        d.pop(col, None)
    return d


def table_names_map(conn: sqlite3.Connection) -> dict[str, str]:
    """Devuelve {safe_name: display_name}."""
    try:
        rows = conn.execute("SELECT safe_name, display_name FROM _table_names").fetchall()
        return {r["safe_name"]: r["display_name"] for r in rows}
    except sqlite3.OperationalError:
        return {}


def list_user_tables(conn: sqlite3.Connection) -> list[str]:
    """Tablas de usuario (excluye _prefijadas)."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '\\_%' ESCAPE '\\' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


# ── Auth helpers ──────────────────────────────────────────────────────────────

_SESSION_HOURS = 10

_ALL_SECCIONES = [
    "dashboard", "clientes", "analisis", "trabajos", "pedidos", "remitos",
    "piezas", "stock", "modelos", "fundiciones", "personal", "documentos",
    "presentacion", "proveedores", "admin",
]


def _hash_pwd(salt: str, password: str) -> str:
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()

def _make_salt() -> str:
    return secrets.token_hex(16)

def _verify_pwd(password: str, salt: str, stored: str) -> bool:
    return _hash_pwd(salt, password) == stored


def _get_auth_user(authorization: Optional[str] = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "No autenticado")
    token = authorization.split(" ", 1)[1].strip()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT u.legajo, u.nombre, u.is_admin "
            "FROM _sesiones s JOIN _usuarios u ON u.legajo = s.legajo "
            "WHERE s.token=? AND s.activa=1 AND s.expires_at>? AND u.activo=1",
            (token, datetime.now().isoformat())
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(401, "Sesión inválida o expirada")
    return {"legajo": row[0], "nombre": row[1], "is_admin": bool(row[2])}


def _require_admin(user: dict = Depends(_get_auth_user)) -> dict:
    if not user["is_admin"]:
        raise HTTPException(403, "Se requiere rol administrador")
    return user


# ── Lookup tables (manually maintained, survive sync) ─────────────────────────

def _seed_lookups():
    """Crea y semilla tablas de lookup que no existen en Access."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS TiposOperacionModelo (
            codigo      TEXT PRIMARY KEY,
            descripcion TEXT NOT NULL
        )
    """)
    ops = [
        ("E", "Entrada"),
        ("S", "Salida"),
        ("X", "Baja"),
        ("D", "Devolución"),
        ("C", "Creación"),
        ("R", "Reparación"),
        ("M", "Modificación"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO TiposOperacionModelo (codigo, descripcion) VALUES (?, ?)", ops
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _piezas_config (
            pieza_id              INTEGER PRIMARY KEY,
            pide_probeta_traccion INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _kiosk_config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO _kiosk_config (key, value) VALUES ('config', ?)",
        ('{"interval":60,"enabled":["dash_vencidos","dash_proximos","dash_entregados","rechazos","evol_bar","evol_pct","evol_prod","ranking_rd","ranking_rep"]}',)
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _usuarios (
            legajo        INTEGER PRIMARY KEY,
            nombre        TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            salt          TEXT NOT NULL,
            is_admin      INTEGER NOT NULL DEFAULT 0,
            activo        INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT,
            updated_at    TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _permisos (
            legajo  INTEGER NOT NULL,
            seccion TEXT NOT NULL,
            PRIMARY KEY (legajo, seccion)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _sesiones (
            token      TEXT PRIMARY KEY,
            legajo     INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            activa     INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _personal_ext (
            access_id  INTEGER PRIMARY KEY,
            legajo     TEXT    NOT NULL DEFAULT '',
            apellido   TEXT    NOT NULL DEFAULT '',
            nombre     TEXT    NOT NULL DEFAULT '',
            sector     INTEGER          DEFAULT NULL
        )
    """)
    # Migración: agregar columna sector si la tabla ya existía sin ella
    existing_cols = [r[1] for r in conn.execute("PRAGMA table_info(_personal_ext)").fetchall()]
    if "sector" not in existing_cols:
        conn.execute("ALTER TABLE _personal_ext ADD COLUMN sector INTEGER DEFAULT NULL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _sectores (
            id     INTEGER PRIMARY KEY,
            nombre TEXT NOT NULL
        )
    """)
    for sid, snombre in [(1,"Producción"),(3,"Administración"),(6,"Laboratorio"),
                         (7,"Mantenimiento"),(97,"Desvinculado"),(99,"Dirección")]:
        conn.execute("INSERT OR IGNORE INTO _sectores (id, nombre) VALUES (?,?)", (sid, snombre))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _personal_ausentismo (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            access_id         INTEGER NOT NULL,
            fecha             TEXT NOT NULL,
            dias_laborables   INTEGER NOT NULL DEFAULT 0,
            enfermedad        REAL    NOT NULL DEFAULT 0,
            falta_con_aviso   REAL    NOT NULL DEFAULT 0,
            falta_sin_aviso   REAL    NOT NULL DEFAULT 0,
            accidente         REAL    NOT NULL DEFAULT 0,
            vacaciones        REAL    NOT NULL DEFAULT 0,
            llegada_tarde     INTEGER NOT NULL DEFAULT 0,
            retiro_anticipado INTEGER NOT NULL DEFAULT 0,
            UNIQUE(access_id, fecha)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _responsables_extra (
            codigoresponsable   INTEGER PRIMARY KEY,
            apellidoynombre     TEXT NOT NULL DEFAULT '',
            nombre              TEXT NOT NULL DEFAULT '',
            apellido            TEXT NOT NULL DEFAULT '',
            codsector           INTEGER NOT NULL DEFAULT 0,
            cargo               TEXT NOT NULL DEFAULT '',
            comentarios         TEXT NOT NULL DEFAULT ''
        )
    """)
    # INSERT OR IGNORE: si ya existe no sobreescribe
    conn.execute("""
        INSERT OR IGNORE INTO _responsables_extra
            (codigoresponsable, apellidoynombre, nombre, apellido, codsector)
        VALUES (9001, 'Giordano Agustin', 'Agustin', 'Giordano', 1)
    """)
    # Vista que unifica Responsables (Access) con _responsables_extra (locales)
    conn.execute("DROP VIEW IF EXISTS _v_responsables")
    conn.execute("""
        CREATE VIEW _v_responsables AS
            SELECT "códigoresponsable"       AS codigoresponsable,
                   apellidoynombreresponsable AS apellidoynombre,
                   nombre, apellido,
                   "códsector"               AS codsector,
                   cargo, comentarios
            FROM Responsables
            UNION ALL
            SELECT codigoresponsable, apellidoynombre, nombre, apellido,
                   codsector, cargo, comentarios
            FROM _responsables_extra
    """)
    # Admin por defecto si no hay usuarios
    if conn.execute("SELECT COUNT(*) FROM _usuarios").fetchone()[0] == 0:
        salt = _make_salt()
        conn.execute(
            "INSERT INTO _usuarios (legajo, nombre, password_hash, salt, is_admin, activo, created_at) "
            "VALUES (?, ?, ?, ?, 1, 1, ?)",
            (0, "Administrador", _hash_pwd(salt, "admin123"), salt, datetime.now().isoformat())
        )
        for sec in _ALL_SECCIONES:
            conn.execute("INSERT OR IGNORE INTO _permisos (legajo, seccion) VALUES (0, ?)", (sec,))
    # Correcciones sobre TiposMaterial (Access tiene datos incompletos/incorrectos).
    # Se aplican en cada arranque para sobrevivir sincronizaciones.
    _tipos_material_fixes = [
        ("1",  "Común Finos"),
        ("3",  "Común"),
        ("5",  "Gris"),
        ("8",  "Nodular"),
        ("12", "Perlítico"),
    ]
    try:
        for cod, nombre in _tipos_material_fixes:
            conn.execute(
                "UPDATE TiposMaterial SET sobrenombrematerial = ? WHERE códmaterial = ?",
                (nombre, cod)
            )
    except Exception:
        pass  # TiposMaterial puede no existir en installs frescas

    # ── Módulo Proveedores ISO 9001 ────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _prov_grupos (
            id     TEXT PRIMARY KEY,
            nombre TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _prov_unidades (
            id          TEXT PRIMARY KEY,
            descripcion TEXT NOT NULL DEFAULT ''
        )
    """)
    for uid, udesc in [("Kg","Kilogramo"),("Tn","Tonelada"),("Un","Unidad"),("L","Litro")]:
        conn.execute("INSERT OR IGNORE INTO _prov_unidades (id, descripcion) VALUES (?,?)", (uid, udesc))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _prov_proveedores (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre         TEXT NOT NULL,
            rubros         TEXT DEFAULT '',
            grupos         TEXT DEFAULT '',
            nivel          TEXT DEFAULT 'sin datos en el ultimo año',
            valoracion     TEXT DEFAULT '#DIV/0!',
            cant_recibida  TEXT DEFAULT '0',
            cant_observada TEXT DEFAULT '0',
            cant_prueba    TEXT DEFAULT '0',
            vencimiento    TEXT DEFAULT '-',
            observaciones  TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _prov_productos (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre           TEXT NOT NULL,
            descripcion      TEXT DEFAULT '',
            rubro            TEXT DEFAULT '',
            grupo            TEXT DEFAULT '',
            proveedor_id     INTEGER REFERENCES _prov_proveedores(id),
            proveedor_nombre TEXT DEFAULT '',
            cantidad_ref     TEXT DEFAULT '',
            etp              TEXT DEFAULT '',
            critico          INTEGER DEFAULT 0,
            unidad           TEXT DEFAULT 'Kg'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _prov_etp (
            codigo    TEXT PRIMARY KEY,
            producto  TEXT DEFAULT '',
            revision  TEXT DEFAULT '',
            estado    TEXT DEFAULT 'Vigente',
            detalle   TEXT DEFAULT '',
            pdf_path  TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _prov_psp (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            producto        TEXT NOT NULL,
            proveedor       TEXT NOT NULL,
            grupo           TEXT DEFAULT '',
            etp             TEXT DEFAULT '',
            fecha           TEXT NOT NULL,
            cant_recibida   REAL NOT NULL DEFAULT 0,
            unidad          TEXT DEFAULT 'Kg',
            cant_observada  TEXT DEFAULT '-',
            motivo          TEXT DEFAULT '-',
            tiene_remito    INTEGER DEFAULT 1,
            control_visual  INTEGER DEFAULT 1,
            tiene_informe   INTEGER DEFAULT 0,
            partida         TEXT DEFAULT '',
            remito          TEXT DEFAULT '',
            observaciones   TEXT DEFAULT '',
            archivo_informe TEXT DEFAULT '',
            created_at      TEXT
        )
    """)
    conn.commit()
    conn.close()


# ── Lifespan ──────────────────────────────────────────────────────────────────

def _run_warmup() -> None:
    """Pre-load gnc.db pages into the OS page cache so the first user request is fast."""
    if not DB_PATH.exists():
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        # Warm Remitos + ItemDetalle + PesosDePiezas + NombreDePiezas (entregas_pieza)
        conn.execute(
            'SELECT COUNT(*) FROM Remitos r'
            ' JOIN ItemDetalle id ON id.idnroremito = r.idnroremito'
            ' JOIN PesosDePiezas pp ON pp."códpieza" = id."códpieza"'
            ' JOIN NombreDePiezas np ON np.id = pp."nombredepiezasid_"'
            ' WHERE r.idnroremito > 10000 AND r.idnroremito < 90000'
        ).fetchone()
        # Warm Trabajos + FundiciónPorFecha + PesosDePiezas + NombreDePiezas (semanal)
        conn.execute(
            'SELECT COUNT(*) FROM Trabajos t'
            ' JOIN "FundiciónPorFecha" fpf ON fpf."códfundición" = t."códfundición"'
            ' LEFT JOIN PesosDePiezas pp ON pp."códpieza" = t.idpesopieza'
            ' LEFT JOIN NombreDePiezas np ON np.id = pp."nombredepiezasid_"'
            ' WHERE fpf.fecha IS NOT NULL'
        ).fetchone()
        conn.close()
        log.info("DB warmup completo")
    except Exception as e:
        log.warning("DB warmup error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import sync as sync_module

    _seed_lookups()

    # Inicializar sub-dict de estado por grupo
    for group in sync_module.SYNC_GROUPS:
        sync_state["groups"][group] = {"last_sync": None, "tables_ok": 0}

    if _should_auto_sync():
        log.info("Auto-sync: iniciando sync completo (primera vez o ≥10 h sin sync)")
        asyncio.create_task(run_sync())
    else:
        log.info("Auto-sync: omitido (sync reciente detectado)")
        sync_state["status"] = "ready"

    asyncio.create_task(_group_scheduler())

    import threading
    threading.Thread(target=_run_warmup, daemon=True).start()
    yield


app = FastAPI(title="piezas.fundicionjoseromero.com", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# JS/CSS compartidos entre index.html y kiosk.html (kiosk_server.py monta las
# mismas carpetas por separado, ya que corre en otro proceso/puerto) -- una
# sola fuente en disco, dos rutas HTTP porque son dos servidores distintos.
_js_dir = FRONTEND / "js"
if _js_dir.exists():
    app.mount("/js", StaticFiles(directory=str(_js_dir)), name="js")
_css_dir = FRONTEND / "css"
if _css_dir.exists():
    app.mount("/css", StaticFiles(directory=str(_css_dir)), name="css")


# ── Endpoints API ─────────────────────────────────────────────────────────────

@app.get("/api/sync/status")
def sync_status():
    return sync_state


@app.post("/api/sync")
async def trigger_sync():
    if sync_state["status"] == "syncing":
        raise HTTPException(409, "Sync ya en progreso")
    asyncio.create_task(run_sync())
    return {"message": "Sync iniciado"}


@app.post("/api/sync/full")
async def trigger_full_sync():
    """Re-descarga TODAS las tablas desde Access ignorando watermarks."""
    if sync_state["status"] == "syncing":
        raise HTTPException(409, "Sync ya en progreso")
    asyncio.create_task(run_sync(full_refresh=True))
    return {"message": "Full sync iniciado — todas las tablas se re-descargarán desde Access"}


@app.post("/api/sync/group/{group_name}")
async def trigger_group_sync(group_name: str):
    """Fuerza el sync manual de un grupo específico (en_curso, movimientos, resoluciones, maestros)."""
    import sync as sync_module
    if group_name not in sync_module.SYNC_GROUPS:
        raise HTTPException(404, f"Grupo '{group_name}' desconocido. "
                                  f"Grupos válidos: {list(sync_module.SYNC_GROUPS.keys())}")
    if sync_state["status"] == "syncing":
        raise HTTPException(409, "Sync ya en progreso")
    asyncio.create_task(run_sync_group(group_name))
    return {"message": f"Sync del grupo '{group_name}' iniciado",
            "tables": sync_module.SYNC_GROUPS[group_name]}


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.post("/api/auth/login")
async def auth_login(req: Request):
    body     = await req.json()
    login    = str(body.get("login") or body.get("legajo", "")).strip()
    password = str(body.get("password", ""))
    if not login:
        raise HTTPException(400, "Legajo o nombre requerido")
    if not password:
        raise HTTPException(400, "Contraseña requerida")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # Buscar por legajo si es numérico, por nombre si es texto
        try:
            legajo_int = int(login)
            u = conn.execute(
                "SELECT legajo, nombre, password_hash, salt, is_admin FROM _usuarios "
                "WHERE legajo=? AND activo=1", (legajo_int,)
            ).fetchone()
        except ValueError:
            u = conn.execute(
                "SELECT legajo, nombre, password_hash, salt, is_admin FROM _usuarios "
                "WHERE lower(nombre)=lower(?) AND activo=1", (login,)
            ).fetchone()
        if not u or not _verify_pwd(password, u["salt"], u["password_hash"]):
            raise HTTPException(401, "Legajo o contraseña incorrectos")
        legajo  = u["legajo"]
        token   = secrets.token_hex(32)
        now     = datetime.now()
        expires = (now + timedelta(hours=_SESSION_HOURS)).isoformat()
        conn.execute(
            "INSERT INTO _sesiones (token, legajo, created_at, expires_at, activa) VALUES (?,?,?,?,1)",
            (token, legajo, now.isoformat(), expires)
        )
        conn.commit()
        secciones = [r[0] for r in conn.execute(
            "SELECT seccion FROM _permisos WHERE legajo=?", (legajo,)
        ).fetchall()]
    finally:
        conn.close()
    return {"token": token, "user": {
        "legajo": u["legajo"], "nombre": u["nombre"],
        "is_admin": bool(u["is_admin"]), "secciones": secciones
    }}


@app.post("/api/auth/logout")
def auth_logout(user: dict = Depends(_get_auth_user),
                authorization: Optional[str] = Header(None)):
    token = (authorization or "").split(" ", 1)[-1].strip()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE _sesiones SET activa=0 WHERE token=?", (token,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(user: dict = Depends(_get_auth_user)):
    conn = sqlite3.connect(DB_PATH)
    secciones = [r[0] for r in conn.execute(
        "SELECT seccion FROM _permisos WHERE legajo=?", (user["legajo"],)
    ).fetchall()]
    conn.close()
    return {**user, "secciones": secciones}


# ── Admin: usuarios ───────────────────────────────────────────────────────────

@app.get("/api/admin/responsables")
def admin_responsables(admin: dict = Depends(_require_admin)):
    """Devuelve Responsables activos (excluye desvinculados y entradas de sistema)."""
    conn = get_db()
    try:
        rows = conn.execute(
            'SELECT "códigoresponsable" AS legajo, "apellidoynombreresponsable" AS nombre '
            'FROM Responsables '
            'WHERE "códigoresponsable" > 0 '
            "AND (comentarios IS NULL OR LOWER(comentarios) != 'desvinculado') "
            'ORDER BY nombre'
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/admin/usuarios")
def admin_get_usuarios(admin: dict = Depends(_require_admin)):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT legajo, nombre, is_admin, activo, created_at, updated_at FROM _usuarios ORDER BY nombre"
        ).fetchall()
        result = []
        for u in rows:
            secciones = [r[0] for r in conn.execute(
                "SELECT seccion FROM _permisos WHERE legajo=?", (u["legajo"],)
            ).fetchall()]
            result.append({**dict(u), "secciones": secciones})
        return result
    finally:
        conn.close()


@app.post("/api/admin/usuarios")
async def admin_create_usuario(req: Request, admin: dict = Depends(_require_admin)):
    body = await req.json()
    legajo   = int(body.get("legajo", -1))
    nombre   = str(body.get("nombre", "")).strip()
    password = str(body.get("password", "")).strip()
    is_adm   = bool(body.get("is_admin", False))
    secciones = body.get("secciones", [])
    if legajo < 0:
        raise HTTPException(400, "Legajo inválido")
    if not nombre:
        raise HTTPException(400, "Nombre requerido")
    if not password:
        raise HTTPException(400, "Contraseña requerida")
    salt = _make_salt()
    now  = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = conn.execute("SELECT legajo FROM _usuarios WHERE legajo=?", (legajo,)).fetchone()
        if existing:
            raise HTTPException(409, f"Ya existe un usuario con legajo {legajo}")
        conn.execute(
            "INSERT INTO _usuarios (legajo, nombre, password_hash, salt, is_admin, activo, created_at) "
            "VALUES (?,?,?,?,?,1,?)",
            (legajo, nombre, _hash_pwd(salt, password), salt, 1 if is_adm else 0, now)
        )
        conn.execute("DELETE FROM _permisos WHERE legajo=?", (legajo,))
        for sec in secciones:
            if sec in _ALL_SECCIONES:
                conn.execute("INSERT OR IGNORE INTO _permisos (legajo, seccion) VALUES (?,?)", (legajo, sec))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "legajo": legajo}


@app.put("/api/admin/usuarios/{legajo}")
async def admin_update_usuario(legajo: int, req: Request, admin: dict = Depends(_require_admin)):
    body = await req.json()
    conn = sqlite3.connect(DB_PATH)
    try:
        u = conn.execute("SELECT legajo FROM _usuarios WHERE legajo=?", (legajo,)).fetchone()
        if not u:
            raise HTTPException(404, "Usuario no encontrado")
        updates = {}
        if "nombre"   in body: updates["nombre"]   = str(body["nombre"]).strip()
        if "is_admin" in body: updates["is_admin"]  = 1 if body["is_admin"] else 0
        if "activo"   in body: updates["activo"]    = 1 if body["activo"] else 0
        if updates:
            updates["updated_at"] = datetime.now().isoformat()
            set_clause = ", ".join(f"{k}=?" for k in updates)
            conn.execute(f"UPDATE _usuarios SET {set_clause} WHERE legajo=?",
                         list(updates.values()) + [legajo])
        if "secciones" in body:
            conn.execute("DELETE FROM _permisos WHERE legajo=?", (legajo,))
            for sec in body["secciones"]:
                if sec in _ALL_SECCIONES:
                    conn.execute("INSERT OR IGNORE INTO _permisos (legajo, seccion) VALUES (?,?)", (legajo, sec))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.post("/api/admin/usuarios/{legajo}/password")
async def admin_reset_password(legajo: int, req: Request, admin: dict = Depends(_require_admin)):
    body = await req.json()
    password = str(body.get("password", "")).strip()
    if len(password) < 4:
        raise HTTPException(400, "La contraseña debe tener al menos 4 caracteres")
    salt = _make_salt()
    conn = sqlite3.connect(DB_PATH)
    try:
        u = conn.execute("SELECT legajo FROM _usuarios WHERE legajo=?", (legajo,)).fetchone()
        if not u:
            raise HTTPException(404, "Usuario no encontrado")
        conn.execute(
            "UPDATE _usuarios SET password_hash=?, salt=?, updated_at=? WHERE legajo=?",
            (_hash_pwd(salt, password), salt, datetime.now().isoformat(), legajo)
        )
        conn.execute("UPDATE _sesiones SET activa=0 WHERE legajo=?", (legajo,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/admin/secciones")
def admin_secciones(admin: dict = Depends(_require_admin)):
    return _ALL_SECCIONES


@app.get("/api/tables")
def get_tables():
    conn = get_db()
    try:
        names_map = table_names_map(conn)
        tables    = list_user_tables(conn)
        result    = []
        for t in tables:
            count = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            result.append({
                "name":         t,
                "display_name": names_map.get(t, t),
                "row_count":    count,
            })
        return result
    finally:
        conn.close()


@app.get("/api/tables/{table_name}")
def get_table_rows(
    table_name: str,
    page:      int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    search:    Optional[str] = Query(None),
    sort:      Optional[str] = Query(None),
    dir:       str = Query("asc", pattern="^(asc|desc)$"),
):
    conn = get_db()
    try:
        tables = list_user_tables(conn)
        if table_name not in tables:
            raise HTTPException(404, f"Tabla '{table_name}' no encontrada")

        # Obtener columnas
        cols_info = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        col_names = [c["name"] for c in cols_info]

        # Obtener nombres originales de columna
        names_map = table_names_map(conn)
        col_display: dict[str, str] = {}
        try:
            meta_row = conn.execute(
                "SELECT columns_json FROM _table_names WHERE safe_name = ?", (table_name,)
            ).fetchone()
            if meta_row:
                col_map = json.loads(meta_row["columns_json"])
                col_display = {safe: orig for safe, orig in col_map}
        except Exception:
            pass

        # Construir WHERE para búsqueda full-text
        where_clause = ""
        params: list = []
        if search and search.strip():
            conditions = [f'CAST("{c}" AS TEXT) LIKE ?' for c in col_names]
            where_clause = "WHERE " + " OR ".join(conditions)
            params = [f"%{search.strip()}%"] * len(col_names)

        # ORDER BY
        order_clause = ""
        if sort and sort in col_names:
            direction = "DESC" if dir.lower() == "desc" else "ASC"
            order_clause = f'ORDER BY "{sort}" {direction}'

        # COUNT total
        total = conn.execute(
            f'SELECT COUNT(*) FROM "{table_name}" {where_clause}', params
        ).fetchone()[0]

        # Paginación
        offset = (page - 1) * page_size
        rows = conn.execute(
            f'SELECT * FROM "{table_name}" {where_clause} {order_clause} LIMIT ? OFFSET ?',
            params + [page_size, offset],
        ).fetchall()

        return {
            "table":        table_name,
            "display_name": names_map.get(table_name, table_name),
            "columns":      [{"key": c, "label": col_display.get(c, c)} for c in col_names],
            "rows":         [dict(r) for r in rows],
            "total":        total,
            "page":         page,
            "page_size":    page_size,
            "pages":        max(1, (total + page_size - 1) // page_size),
        }
    finally:
        conn.close()


@app.get("/api/tables/{table_name}/export")
def export_table_csv(table_name: str):
    import csv
    import io

    conn = get_db()
    try:
        tables = list_user_tables(conn)
        if table_name not in tables:
            raise HTTPException(404, f"Tabla '{table_name}' no encontrada")

        names_map = table_names_map(conn)
        display   = names_map.get(table_name, table_name)

        cols_info = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        col_names = [c["name"] for c in cols_info]

        rows = conn.execute(f'SELECT * FROM "{table_name}"').fetchall()

        def generate():
            buf = io.StringIO()
            w   = csv.writer(buf)
            w.writerow(col_names)
            yield buf.getvalue()
            buf.seek(0); buf.truncate()

            for row in rows:
                w.writerow(list(row))
                yield buf.getvalue()
                buf.seek(0); buf.truncate()

        safe_display = display.replace('"', '').replace("'", "")
        return StreamingResponse(
            generate(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_display}.csv"'},
        )
    finally:
        conn.close()


# ── Analytics ─────────────────────────────────────────────────────────────────

# Defect columns in ItemDevolución (INTEGER quantity columns, excludes TEXT flags)
_DEFECT_NAMES = {
    "rechupe": "Rechupe", "basura": "Basura", "poros": "Poros",
    "rotas": "Rotas", "sopladas": "Sopladas", "duras": "Duras",
    "torcidas": "Torcidas", "granoabierto": "Grano abierto",
    "moldeo": "Moldeo", "deformadas": "Deformadas", "variadas": "Variadas",
    "hinchadas": "Hinchadas", "coladarota": "Colada rota",
    "noyofiltrado": "Noyo filtrado", "faltademateriales": "Falta materiales",
    "noyomalcolocado": "Noyo mal colocado", "sinfallas": "Sin fallas",
}


def _defect_cols(conn: sqlite3.Connection) -> list[str]:
    existing = {c["name"] for c in conn.execute('PRAGMA table_info("ItemDevolución")').fetchall()}
    return [c for c in _DEFECT_NAMES if c in existing]


def _sum_defects(conn: sqlite3.Connection, pieza_id: int) -> list[dict]:
    cols = _defect_cols(conn)
    if not cols:
        return []
    expr = ", ".join(f'COALESCE(SUM(id."{c}"), 0) as "{c}"' for c in cols)
    row = conn.execute(
        f'SELECT {expr} FROM "ItemDevolución" id '
        f'JOIN PesosDePiezas pp ON id.códpieza = pp.códpieza '
        f'WHERE pp.nombredepiezasid_ = ?',
        (pieza_id,),
    ).fetchone()
    result = [{"tipo": c, "label": _DEFECT_NAMES[c], "total": row[c]} for c in cols if row[c] > 0]
    result.sort(key=lambda x: x["total"], reverse=True)
    return result


_PROXIMOS_SELECT = """
    SELECT
        p.idpedido, p.nropedido, p.códigocliente,
        COALESCE(c.nombrefantasía, c.nombrecliente) AS cliente_nombre,
        np.nombrepieza, np.códigopiezapuestoporcliente AS codigo_pieza,
        idp.iditempedido, idp.cantidadpedida, idp.estadoitem,
        date(idp.fechadeentrega) AS fecha_entrega,
        CAST(julianday(date(idp.fechadeentrega)) - julianday('now') AS INTEGER) AS dias,
        last_ot.ot_id,
        t_last.estadotrabajo AS ot_estado,
        qty.tot_producida,
        qty.tot_rechazada,
        qty.tot_entregada,
        COALESCE(pc.pide_probeta_traccion, 0) AS pide_probeta_traccion
    FROM ItemDetallePedido idp
    JOIN Pedidos p ON idp.idpedido = p.idpedido
    JOIN Clientes c ON p.códigocliente = c.códigocliente
    JOIN NombreDePiezas np ON idp.idpieza = np.id
    LEFT JOIN (
        SELECT iditempedido, MAX(iditemtrabajo) AS ot_id
        FROM Trabajos
        GROUP BY iditempedido
    ) last_ot ON last_ot.iditempedido = idp.iditempedido
    LEFT JOIN Trabajos t_last ON t_last.iditemtrabajo = last_ot.ot_id
    LEFT JOIN (
        SELECT iditempedido,
               COALESCE(SUM(cantidadentregada), 0) AS tot_entregada,
               COALESCE(SUM(cantidadproducida),  0) AS tot_producida,
               COALESCE(SUM(cantidadrechazada),  0) AS tot_rechazada
        FROM Trabajos
        GROUP BY iditempedido
    ) qty ON qty.iditempedido = idp.iditempedido
    LEFT JOIN _piezas_config pc ON pc.pieza_id = np.id
    WHERE idp.fechadeentrega IS NOT NULL
      AND upper(p.estadopedido) NOT IN ('K','D')
      AND upper(idp.estadoitem) NOT IN ('K','D')
      AND COALESCE(qty.tot_entregada, 0) = 0
"""

def _resolve_ot_estados(rows: list, conn: sqlite3.Connection) -> list:
    est = _est_map(conn)
    result = []
    for r in rows:
        d = dict(r)
        code = d.get("ot_estado") or ""
        d["ot_estado"] = est.get(code, code) if code else None
        result.append(d)
    return result


@app.get("/api/dashboard/proximos")
def dashboard_proximos():
    conn = get_db()
    try:
        rows = conn.execute(
            _PROXIMOS_SELECT +
            "  AND date(idp.fechadeentrega) BETWEEN date('now') AND date('now', '+30 days')"
            " ORDER BY idp.fechadeentrega ASC LIMIT 50"
        ).fetchall()
        return _resolve_ot_estados(rows, conn)
    finally:
        conn.close()


@app.get("/api/dashboard/vencidos")
def dashboard_vencidos(meses: int = 2):
    meses = max(1, min(meses, 60))
    conn = get_db()
    try:
        rows = conn.execute(
            _PROXIMOS_SELECT +
            "  AND date(idp.fechadeentrega) BETWEEN date('now', ? || ' months') AND date('now', '-1 days')"
            " ORDER BY idp.fechadeentrega ASC LIMIT 50",
            (f"-{meses}",)
        ).fetchall()
        return _resolve_ot_estados(rows, conn)
    finally:
        conn.close()


@app.get("/api/dashboard/entregados")
def dashboard_entregados(meses: int = 6):
    meses = max(1, min(meses, 60))
    conn = get_db()
    try:
        est = _est_map(conn)
        rows = conn.execute("""
            SELECT
                p.idpedido, p.nropedido, p.códigocliente,
                COALESCE(c.nombrefantasía, c.nombrecliente) AS cliente_nombre,
                np.nombrepieza, np.códigopiezapuestoporcliente AS codigo_pieza,
                idp.iditempedido, idp.cantidadpedida,
                date(idp.fechadeentrega) AS fecha_entrega,
                qty.tot_entregada,
                qty.tot_producida,
                last_ot.ot_id,
                t_last.estadotrabajo AS ot_estado,
                COALESCE(pc.pide_probeta_traccion, 0) AS pide_probeta_traccion
            FROM ItemDetallePedido idp
            JOIN Pedidos p ON idp.idpedido = p.idpedido
            JOIN Clientes c ON p.códigocliente = c.códigocliente
            JOIN NombreDePiezas np ON idp.idpieza = np.id
            LEFT JOIN (
                SELECT iditempedido, MAX(iditemtrabajo) AS ot_id
                FROM Trabajos GROUP BY iditempedido
            ) last_ot ON last_ot.iditempedido = idp.iditempedido
            LEFT JOIN Trabajos t_last ON t_last.iditemtrabajo = last_ot.ot_id
            LEFT JOIN (
                SELECT iditempedido,
                       COALESCE(SUM(cantidadentregada), 0) AS tot_entregada,
                       COALESCE(SUM(cantidadproducida),  0) AS tot_producida
                FROM Trabajos GROUP BY iditempedido
            ) qty ON qty.iditempedido = idp.iditempedido
            LEFT JOIN _piezas_config pc ON pc.pieza_id = np.id
            WHERE idp.fechadeentrega IS NOT NULL
              AND qty.tot_entregada > 0
              AND date(idp.fechadeentrega) BETWEEN date('now', ? || ' months') AND date('now', '+30 days')
            ORDER BY
                CASE WHEN qty.tot_entregada >= idp.cantidadpedida THEN 1 ELSE 0 END ASC,
                idp.fechadeentrega DESC
            LIMIT 200
        """, (f"-{meses}",)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            code = d.get("ot_estado") or ""
            d["ot_estado"] = est.get(code, code) if code else None
            result.append(d)
        return result
    finally:
        conn.close()


# PesosDePiezas tiene, por pieza, varias filas (una por variante de moldeo) y
# Trabajos.idpesopieza apunta a UNA variante puntual -- que en muchos casos quedó
# sin asignar aunque la pieza sí tenga peso cargado en otras variantes (ver
# dashboard_pendiente_fundir_sin_peso). -1 aparece como valor centinela de
# "todavía no pesado" en algunas filas viejas, por eso el filtro > 0 en vez de
# IS NOT NULL. Ambos endpoints resuelven primero por la variante exacta de la OT
# y recién si esa no existe caen al promedio de las variantes válidas de la pieza.
_CTE_PIEZAS_PESO = """
    WITH piezas_peso AS (
        SELECT nombredepiezasid_ AS pieza_id, AVG(pesopieza) AS peso_promedio
        FROM PesosDePiezas
        WHERE pesopieza > 0
        GROUP BY nombredepiezasid_
    )
"""

# Batiplane queda afuera de pendiente_fundir/calendario/detalle salvo pedido explícito
# (ver dashboard_pendiente_fundir) -- su código de cliente es fijo, no una config editable.
_COD_CLIENTE_BATIPLANE = "BA3"


@app.get("/api/dashboard/pendiente_fundir")
def dashboard_pendiente_fundir(meses: int = 6):
    """Pendiente de fundir por material, separado en Programado (nada fundido
    todavía, falta fundir) y Fundido (ya fundido, falta entregar) -- mismo
    criterio de "pendiente" por estado que dashboard_proyeccion_pipeline, acá
    agrupado por material además de por estado. Cada campo programado/fundido
    trae además batiplane: el mismo total pero solo la porción de Batiplane
    (BA3) -- su volumen aparte se resalta con otro color en vez de excluirse,
    para no esconder ninguna OT pendiente de fundir de un vistazo."""
    meses = max(1, min(meses, 60))
    conn = get_db()
    try:
        # Antigüedad de la OT: fechacargaot es NULL en ~93% de las OTs activas pendientes de
        # fundir (a diferencia de datos históricas ya cerradas), así que igual que en
        # analytics_top_defectos/analytics_top_piezas usamos p.fechapedido como respaldo.
        rows = conn.execute(_CTE_PIEZAS_PESO + """
            , base AS (
                SELECT
                    t."códdeagregados" AS codigo,
                    COALESCE(tm.sobrenombrematerial, t."códdeagregados") AS material,
                    t.estadotrabajo AS estado,
                    CASE WHEN upper(p."códigocliente") = ? THEN 1 ELSE 0 END AS es_batiplane,
                    CASE WHEN upper(t.estadotrabajo) = 'P'
                         THEN (t.cantidad - COALESCE(t.cantidadfundida, 0))
                         ELSE COALESCE(t.cantidadproducida, 0) END AS pendientes,
                    np.nombrepieza AS nombrepieza,
                    COALESCE(np.coefcompl, 1) AS coefcompl,
                    CASE WHEN pp.pesopieza > 0 THEN pp.pesopieza ELSE pz.peso_promedio END AS peso_efectivo
                FROM Trabajos t
                JOIN ItemDetallePedido idp ON idp.iditempedido = t.iditempedido
                JOIN Pedidos p             ON p.idpedido = idp.idpedido
                LEFT JOIN TiposMaterial tm ON tm."códmaterial" = CAST(t."códdeagregados" AS TEXT)
                LEFT JOIN NombreDePiezas np ON np.id = idp.idpieza
                LEFT JOIN PesosDePiezas pp  ON pp.códpieza = t.idpesopieza
                LEFT JOIN piezas_peso pz    ON pz.pieza_id = np.id
                WHERE upper(t.estadotrabajo) IN ('P', 'F')
                  AND upper(p.estadopedido)  NOT IN ('K','D')
                  AND upper(idp.estadoitem)  NOT IN ('K','D')
                  AND t."códdeagregados" NOT IN ('--', '4', 'Ar', 'ar', 'AR')
                  AND COALESCE(t.fechacargaot, p.fechapedido) >= date('now', ? || ' months')
            )
            SELECT
                codigo,
                material,
                estado,
                es_batiplane,
                COUNT(*) AS ots,
                SUM(pendientes) AS piezas_pendientes,
                ROUND(SUM(pendientes *
                    CASE WHEN nombrepieza LIKE '%(AD)' THEN peso_efectivo * 2
                         WHEN nombrepieza LIKE '%(TR)' THEN peso_efectivo * 3
                         ELSE peso_efectivo END * coefcompl), 2) AS kg_pendientes,
                SUM(CASE WHEN peso_efectivo IS NULL THEN 1 ELSE 0 END) AS ots_sin_peso,
                SUM(CASE WHEN peso_efectivo IS NULL THEN pendientes ELSE 0 END) AS piezas_sin_peso
            FROM base
            GROUP BY codigo, estado, es_batiplane
            ORDER BY codigo
        """, (_COD_CLIENTE_BATIPLANE, f"-{meses}")).fetchall()

        def vacio():
            return {"ots": 0, "piezas_pendientes": 0, "kg_pendientes": 0, "ots_sin_peso": 0, "piezas_sin_peso": 0}

        por_material: dict[str, dict] = {}
        for r in rows:
            d = dict(r)
            m = por_material.setdefault(d["codigo"], {
                "codigo": d["codigo"], "material": d["material"],
                "programado": vacio(), "fundido": vacio(),
            })
            campo = "programado" if d["estado"] == "P" else "fundido"
            seg = m[campo]
            seg["ots"] += d["ots"]
            seg["piezas_pendientes"] += d["piezas_pendientes"]
            seg["kg_pendientes"] = round((seg["kg_pendientes"] or 0) + (d["kg_pendientes"] or 0), 2)
            seg["ots_sin_peso"] += d["ots_sin_peso"]
            seg["piezas_sin_peso"] += d["piezas_sin_peso"]
            if d["es_batiplane"]:
                seg["batiplane"] = {
                    "ots": d["ots"], "piezas_pendientes": d["piezas_pendientes"],
                    "kg_pendientes": d["kg_pendientes"] or 0,
                }

        for m in por_material.values():
            for campo in ("programado", "fundido"):
                m[campo].setdefault("batiplane", {"ots": 0, "piezas_pendientes": 0, "kg_pendientes": 0})

        resultado = list(por_material.values())
        resultado.sort(key=lambda m: m["programado"]["ots"] + m["fundido"]["ots"], reverse=True)
        return resultado
    finally:
        conn.close()


@app.get("/api/dashboard/pendiente_fundir/calendario")
def dashboard_pendiente_fundir_calendario(meses: int = 6):
    """Igual base que dashboard_pendiente_fundir (mismo criterio de "pendiente"
    y misma ventana de antigüedad de OT) pero agregado por fecha de entrega
    (fechaprevista) en vez de por material -- para el modo calendario: cuanto
    Programado/Fundido (OTs y kg) vence cada día, sumando todos los materiales.
    Cada campo programado/fundido trae además batiplane, igual que en
    dashboard_pendiente_fundir."""
    meses = max(1, min(meses, 60))
    conn = get_db()
    try:
        cte_base = _CTE_PIEZAS_PESO + """
            , base AS (
                SELECT
                    t.estadotrabajo AS estado,
                    date(t.fechaprevista) AS fecha,
                    CASE WHEN upper(p."códigocliente") = ? THEN 1 ELSE 0 END AS es_batiplane,
                    CASE WHEN upper(t.estadotrabajo) = 'P'
                         THEN (t.cantidad - COALESCE(t.cantidadfundida, 0))
                         ELSE COALESCE(t.cantidadproducida, 0) END AS pendientes,
                    np.nombrepieza AS nombrepieza,
                    COALESCE(np.coefcompl, 1) AS coefcompl,
                    CASE WHEN pp.pesopieza > 0 THEN pp.pesopieza ELSE pz.peso_promedio END AS peso_efectivo
                FROM Trabajos t
                JOIN ItemDetallePedido idp ON idp.iditempedido = t.iditempedido
                JOIN Pedidos p             ON p.idpedido = idp.idpedido
                LEFT JOIN NombreDePiezas np ON np.id = idp.idpieza
                LEFT JOIN PesosDePiezas pp  ON pp.códpieza = t.idpesopieza
                LEFT JOIN piezas_peso pz    ON pz.pieza_id = np.id
                WHERE upper(t.estadotrabajo) IN ('P', 'F')
                  AND upper(p.estadopedido)  NOT IN ('K','D')
                  AND upper(idp.estadoitem)  NOT IN ('K','D')
                  AND t."códdeagregados" NOT IN ('--', '4', 'Ar', 'ar', 'AR')
                  AND COALESCE(t.fechacargaot, p.fechapedido) >= date('now', ? || ' months')
            )
        """
        kg_expr = """ROUND(SUM(pendientes *
                    CASE WHEN nombrepieza LIKE '%(AD)' THEN peso_efectivo * 2
                         WHEN nombrepieza LIKE '%(TR)' THEN peso_efectivo * 3
                         ELSE peso_efectivo END * coefcompl), 2)"""
        params = [_COD_CLIENTE_BATIPLANE, f"-{meses}"]

        rows = conn.execute(cte_base + f"""
            SELECT estado, fecha, es_batiplane, COUNT(*) AS ots, SUM(pendientes) AS piezas_pendientes, {kg_expr} AS kg_pendientes
            FROM base
            WHERE fecha IS NOT NULL
            GROUP BY estado, fecha, es_batiplane
        """, params).fetchall()

        sin_fecha_rows = conn.execute(cte_base + f"""
            SELECT estado, es_batiplane, COUNT(*) AS ots, SUM(pendientes) AS piezas_pendientes, {kg_expr} AS kg_pendientes
            FROM base
            WHERE fecha IS NULL
            GROUP BY estado, es_batiplane
        """, params).fetchall()

        def vacio():
            return {"ots": 0, "piezas_pendientes": 0, "kg_pendientes": 0}

        def acumular(destino, d):
            campo = "programado" if d["estado"] == "P" else "fundido"
            seg = destino[campo]
            seg["ots"] += d["ots"]
            seg["piezas_pendientes"] += d["piezas_pendientes"]
            seg["kg_pendientes"] = round((seg["kg_pendientes"] or 0) + (d["kg_pendientes"] or 0), 2)
            if d["es_batiplane"]:
                seg["batiplane"] = {
                    "ots": d["ots"], "piezas_pendientes": d["piezas_pendientes"],
                    "kg_pendientes": d["kg_pendientes"] or 0,
                }

        por_fecha: dict[str, dict] = {}
        for r in rows:
            d = dict(r)
            dia = por_fecha.setdefault(d["fecha"], {"fecha": d["fecha"], "programado": vacio(), "fundido": vacio()})
            acumular(dia, d)

        sin_fecha = {"programado": vacio(), "fundido": vacio()}
        for r in sin_fecha_rows:
            acumular(sin_fecha, dict(r))

        for dia in list(por_fecha.values()) + [sin_fecha]:
            for campo in ("programado", "fundido"):
                dia[campo].setdefault("batiplane", {"ots": 0, "piezas_pendientes": 0, "kg_pendientes": 0})

        return {"dias": sorted(por_fecha.values(), key=lambda x: x["fecha"]), "sin_fecha": sin_fecha}
    finally:
        conn.close()


@app.get("/api/dashboard/pendiente_fundir/detalle")
def dashboard_pendiente_fundir_detalle(
    meses: int = 6,
    codigo: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    fecha: Optional[str] = Query(None),
):
    """Lista de OTs (Trabajos) individuales detras de un numero de
    dashboard_pendiente_fundir (codigo+estado, clic en una barra) o de
    dashboard_pendiente_fundir_calendario (fecha, clic en un dia del
    calendario -- sin estado trae Programadas y Fundidas juntas, cada fila
    ya dice la suya). Mismo criterio de "pendiente" y misma ventana de
    antigüedad que esos dos endpoints, para que la lista SIEMPRE coincida
    exacto con el total que el usuario tocó. Incluye Batiplane -- cada fila
    ya dice de qué cliente es (codigo_cliente/cliente_nombre)."""
    meses = max(1, min(meses, 60))
    conn = get_db()
    try:
        where = [
            "upper(t.estadotrabajo) IN ('P', 'F')",
            "upper(p.estadopedido)  NOT IN ('K','D')",
            "upper(idp.estadoitem)  NOT IN ('K','D')",
            "t.\"códdeagregados\" NOT IN ('--', '4', 'Ar', 'ar', 'AR')",
            "COALESCE(t.fechacargaot, p.fechapedido) >= date('now', ? || ' months')",
        ]
        params: list = [f"-{meses}"]
        if codigo:
            where.append('t."códdeagregados" = ?')
            params.append(codigo)
        if estado:
            where.append("upper(t.estadotrabajo) = ?")
            params.append(estado.upper())
        if fecha:
            where.append("date(t.fechaprevista) = ?")
            params.append(fecha)

        rows = conn.execute(_CTE_PIEZAS_PESO + f"""
            , base AS (
                SELECT
                    t.iditemtrabajo AS ot_id,
                    t.estadotrabajo AS estado,
                    t.fechaprevista,
                    p.fechapedido,
                    t."códdeagregados" AS codigo_material,
                    COALESCE(tm.sobrenombrematerial, t."códdeagregados") AS material,
                    np.nombrepieza,
                    np.códigopiezapuestoporcliente AS codigo_pieza,
                    p.códigocliente AS codigo_cliente,
                    COALESCE(c.nombrefantasía, c.nombrecliente) AS cliente_nombre,
                    COALESCE(np.coefcompl, 1) AS coefcompl,
                    CASE WHEN upper(t.estadotrabajo) = 'P'
                         THEN (t.cantidad - COALESCE(t.cantidadfundida, 0))
                         ELSE COALESCE(t.cantidadproducida, 0) END AS pendientes,
                    CASE WHEN pp.pesopieza > 0 THEN pp.pesopieza ELSE pz.peso_promedio END AS peso_efectivo
                FROM Trabajos t
                JOIN ItemDetallePedido idp ON idp.iditempedido = t.iditempedido
                JOIN Pedidos p             ON p.idpedido = idp.idpedido
                JOIN Clientes c            ON c.códigocliente = p.códigocliente
                LEFT JOIN TiposMaterial tm ON tm."códmaterial" = CAST(t."códdeagregados" AS TEXT)
                LEFT JOIN NombreDePiezas np ON np.id = idp.idpieza
                LEFT JOIN PesosDePiezas pp  ON pp.códpieza = t.idpesopieza
                LEFT JOIN piezas_peso pz    ON pz.pieza_id = np.id
                WHERE {' AND '.join(where)}
            )
            SELECT
                ot_id, estado, fechaprevista, fechapedido,
                codigo_material, material, nombrepieza, codigo_pieza,
                codigo_cliente, cliente_nombre, pendientes,
                CASE WHEN nombrepieza LIKE '%(AD)' THEN peso_efectivo * 2
                     WHEN nombrepieza LIKE '%(TR)' THEN peso_efectivo * 3
                     ELSE peso_efectivo END * coefcompl AS peso_unitario,
                ROUND(pendientes * (CASE WHEN nombrepieza LIKE '%(AD)' THEN peso_efectivo * 2
                     WHEN nombrepieza LIKE '%(TR)' THEN peso_efectivo * 3
                     ELSE peso_efectivo END * coefcompl), 2) AS kg_pendientes
            FROM base
            ORDER BY fechaprevista IS NULL, fechaprevista, ot_id
        """, params).fetchall()

        est = _est_map(conn)
        result = []
        for r in rows:
            d = dict(r)
            d["estado_label"] = est.get(d["estado"] or "", d["estado"] or "")
            result.append(d)
        return result
    finally:
        conn.close()


# Compartida entre el agregado y el desglose por mes de dashboard_proyeccion_pipeline.
# Programado: nada fundido todavia, lo pendiente es cantidad-cantidadfundida (falta
# fundir). Fundido: ya se fundio, lo pendiente es lo YA producido (falta entregar) --
# por eso NO se filtra por cantidadfundida<cantidad aca, un Fundido tipicamente ya
# tiene cantidadfundida=cantidad.
_CTE_PROYECCION_BASE = """
    , base AS (
        SELECT
            t.estadotrabajo AS estado,
            strftime('%Y', t.fechaprevista) AS año,
            strftime('%m', t.fechaprevista) AS mes,
            CASE WHEN upper(t.estadotrabajo) = 'P'
                 THEN (t.cantidad - COALESCE(t.cantidadfundida, 0))
                 ELSE COALESCE(t.cantidadproducida, 0) END AS pendientes,
            np.nombrepieza AS nombrepieza,
            COALESCE(np.coefcompl, 1) AS coefcompl,
            CASE WHEN pp.pesopieza > 0 THEN pp.pesopieza ELSE pz.peso_promedio END AS peso_efectivo
        FROM Trabajos t
        JOIN ItemDetallePedido idp ON idp.iditempedido = t.iditempedido
        JOIN Pedidos p             ON p.idpedido = idp.idpedido
        LEFT JOIN NombreDePiezas np ON np.id = idp.idpieza
        LEFT JOIN PesosDePiezas pp  ON pp.códpieza = t.idpesopieza
        LEFT JOIN piezas_peso pz    ON pz.pieza_id = np.id
        WHERE upper(t.estadotrabajo) IN ('P', 'F')
          AND upper(p.estadopedido)  NOT IN ('K','D')
          AND upper(idp.estadoitem)  NOT IN ('K','D')
          AND t."códdeagregados" NOT IN ('--', '4', 'Ar', 'ar', 'AR')
    )
"""
_KG_EXPR = """ROUND(SUM(pendientes *
                    CASE WHEN nombrepieza LIKE '%(AD)' THEN peso_efectivo * 2
                         WHEN nombrepieza LIKE '%(TR)' THEN peso_efectivo * 3
                         ELSE peso_efectivo END * coefcompl), 2)"""


@app.get("/api/dashboard/proyeccion_pipeline")
def dashboard_proyeccion_pipeline():
    """OTs Programadas y Fundidas (todo el pipeline activo, sin ventana de meses --
    es una foto de hoy, no algo historico) con su kg, mismo peso_efectivo que
    dashboard_pendiente_fundir. El agregado alimenta la tarjeta KPI y la barra
    anual de Tendencia; por_mes (agrupado por año/mes de fechaprevista, la OT
    puede ser de cualquier año si esta atrasada) alimenta el drill-down mensual,
    para poner cada Programada/Fundida en su mes de entrega en vez de todas
    juntas en el ultimo mes con datos."""
    conn = get_db()
    try:
        rows = conn.execute(_CTE_PIEZAS_PESO + _CTE_PROYECCION_BASE + f"""
            SELECT estado, COUNT(*) AS ots, SUM(pendientes) AS piezas, {_KG_EXPR} AS kg
            FROM base
            GROUP BY estado
        """).fetchall()
        por_estado = {r["estado"]: dict(r) for r in rows}
        vacio = {"ots": 0, "piezas": 0, "kg": 0}

        mes_rows = conn.execute(_CTE_PIEZAS_PESO + _CTE_PROYECCION_BASE + f"""
            SELECT estado, año, mes, COUNT(*) AS ots, SUM(pendientes) AS piezas, {_KG_EXPR} AS kg
            FROM base
            WHERE año IS NOT NULL
            GROUP BY estado, año, mes
            ORDER BY año, mes
        """).fetchall()

        return {
            "programado": por_estado.get("P", vacio),
            "fundido":    por_estado.get("F", vacio),
            "por_mes":    [dict(r) for r in mes_rows],
        }
    finally:
        conn.close()


@app.get("/api/dashboard/pendiente_fundir/sin_peso")
def dashboard_pendiente_fundir_sin_peso(meses: int = 6):
    """Piezas pendientes de fundir que no tienen NINGUNA variante con peso valido
    cargado en PesosDePiezas (a diferencia del kg_pendientes de arriba, que ya
    contempla el promedio de otras variantes de la misma pieza como respaldo) --
    estas son las que hay que ubicar y pesar de verdad.

    De paso registra cada pieza en _piezas_sin_peso (primera_vez/ultima_vez/
    ultima_ot_id) para poder ver hace cuanto viene siendo un problema, no solo
    el estado de hoy -- la tabla ya existia (huerfana, sin ningun codigo que la
    llenara) con exactamente esa forma, asi que se reutiliza en vez de crear
    una nueva."""
    meses = max(1, min(meses, 60))
    conn = get_db()
    try:
        rows = conn.execute(_CTE_PIEZAS_PESO + """
            SELECT
                np.id                                       AS pieza_id,
                np.nombrepieza                              AS nombre,
                np."códigopiezapuestoporcliente"            AS codigo_pieza,
                COALESCE(tm.sobrenombrematerial, t."códdeagregados") AS material,
                COUNT(DISTINCT t.iditemtrabajo)              AS ots,
                SUM(t.cantidad - COALESCE(t.cantidadfundida, 0)) AS piezas_pendientes,
                MAX(t.iditemtrabajo)                         AS ultima_ot_id,
                (SELECT fdm.enlacefotomodelo
                 FROM PiezasPorModelo ppm2
                 JOIN FotosDeModelos fdm
                   ON fdm."códigomodelo" = ppm2."códmodelo"
                  AND fdm.habilitada = 'True'
                 WHERE ppm2."códpieza" = np.id
                 LIMIT 1)                                    AS foto
            FROM Trabajos t
            JOIN ItemDetallePedido idp ON idp.iditempedido = t.iditempedido
            JOIN Pedidos p             ON p.idpedido = idp.idpedido
            LEFT JOIN TiposMaterial tm ON tm."códmaterial" = CAST(t."códdeagregados" AS TEXT)
            LEFT JOIN NombreDePiezas np ON np.id = idp.idpieza
            LEFT JOIN piezas_peso pz   ON pz.pieza_id = np.id
            WHERE upper(t.estadotrabajo) NOT IN ('K','D','A','B')
              AND upper(p.estadopedido)  NOT IN ('K','D')
              AND upper(idp.estadoitem)  NOT IN ('K','D')
              AND COALESCE(t.cantidadfundida, 0) < t.cantidad
              AND t."códdeagregados" NOT IN ('--', '4', 'Ar', 'ar', 'AR')
              AND COALESCE(t.fechacargaot, p.fechapedido) >= date('now', ? || ' months')
              AND pz.peso_promedio IS NULL
            GROUP BY np.id, material
            ORDER BY piezas_pendientes DESC
        """, (f"-{meses}",)).fetchall()
        result = [dict(r) for r in rows]

        pieza_ids = [r["pieza_id"] for r in result if r["pieza_id"] is not None]
        vistas_desde: dict[int, str] = {}
        if pieza_ids:
            hoy = date_cls.today().isoformat()
            qs = ",".join("?" * len(pieza_ids))
            existentes = conn.execute(
                f"SELECT idpieza, primera_vez FROM _piezas_sin_peso WHERE idpieza IN ({qs})",
                pieza_ids,
            ).fetchall()
            vistas_desde = {row["idpieza"]: row["primera_vez"] for row in existentes}
            for r in result:
                conn.execute("""
                    INSERT INTO _piezas_sin_peso (idpieza, nombrepieza, primera_vez, ultima_vez, ultima_ot_id)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(idpieza) DO UPDATE SET
                        nombrepieza  = excluded.nombrepieza,
                        ultima_vez   = excluded.ultima_vez,
                        ultima_ot_id = excluded.ultima_ot_id
                """, (r["pieza_id"], r["nombre"], hoy, hoy, r["ultima_ot_id"]))
                r["visto_desde"] = vistas_desde.get(r["pieza_id"], hoy)
            conn.commit()

        return result
    finally:
        conn.close()


@app.post("/api/piezas/{pieza_id}/probeta_traccion")
def toggle_probeta_traccion(pieza_id: int, activar: bool = True):
    """Activa o desactiva el flag pide_probeta_traccion para una pieza."""
    conn = get_db()
    try:
        # Verificar que la pieza existe
        row = conn.execute("SELECT id FROM NombreDePiezas WHERE id = ?", (pieza_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Pieza {pieza_id} no encontrada")
        conn.execute("""
            INSERT INTO _piezas_config (pieza_id, pide_probeta_traccion)
            VALUES (?, ?)
            ON CONFLICT(pieza_id) DO UPDATE SET pide_probeta_traccion = excluded.pide_probeta_traccion
        """, (pieza_id, 1 if activar else 0))
        conn.commit()
        return {"pieza_id": pieza_id, "pide_probeta_traccion": activar}
    finally:
        conn.close()


@app.get("/api/alertas/probeta_traccion")
def alertas_probeta_traccion(meses: int = 6):
    """Devuelve OTs activas (no entregadas) de piezas con pide_probeta_traccion=1, dentro del período."""
    meses = max(1, min(meses, 60))
    conn = get_db()
    try:
        est = _est_map(conn)
        rows = conn.execute("""
            SELECT
                np.id AS pieza_id,
                np.nombrepieza,
                np.códigopiezapuestoporcliente AS codigo_pieza,
                COALESCE(c.nombrefantasía, c.nombrecliente) AS cliente_nombre,
                t.iditemtrabajo AS ot_id,
                t.estadotrabajo AS ot_estado,
                t.cantidad,
                t.cantidadaprobada,
                date(idp.fechadeentrega) AS fecha_entrega,
                t.códmaterial AS material_id,
                t.códdeagregados AS material_agregados,
                COALESCE(m1.norma, tm.sobrenombrematerial) AS material_norma
            FROM _piezas_config pc
            JOIN NombreDePiezas np ON np.id = pc.pieza_id
            JOIN Clientes c ON c.códigocliente = np.códcliente
            JOIN ItemDetallePedido idp ON idp.idpieza = np.id
            JOIN Pedidos p ON p.idpedido = idp.idpedido
            JOIN Trabajos t ON t.iditempedido = idp.iditempedido
            LEFT JOIN Materiales m1 ON m1."especificaciónmaterial" = t.códmaterial
            LEFT JOIN TiposMaterial tm ON tm.códmaterial = CAST(t.códdeagregados AS TEXT)
            WHERE pc.pide_probeta_traccion = 1
              AND upper(p.estadopedido) NOT IN ('K','D')
              AND upper(idp.estadoitem) NOT IN ('K','D')
              AND COALESCE(t.cantidadentregada, 0) = 0
              AND COALESCE(t.cantidadfundida, 0) = 0
              AND (date(idp.fechadeentrega) IS NULL
                   OR date(idp.fechadeentrega) >= date('now', ? || ' months'))
            ORDER BY fecha_entrega ASC, np.nombrepieza ASC
        """, (f"-{meses}",)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            code = d.get("ot_estado") or ""
            d["ot_estado"] = est.get(code, code) if code else None
            result.append(d)
        return result
    finally:
        conn.close()


@app.post("/api/clientes/{codigo}/probeta_traccion")
def toggle_probeta_traccion_cliente(codigo: str, activar: bool = True):
    """Activa o desactiva pide_probeta_traccion en TODAS las piezas de un cliente."""
    conn = get_db()
    try:
        cl = conn.execute(
            "SELECT códigocliente FROM Clientes WHERE códigocliente = ?", (codigo,)
        ).fetchone()
        if not cl:
            raise HTTPException(404, f"Cliente '{codigo}' no encontrado")
        pieza_ids = conn.execute(
            "SELECT id FROM NombreDePiezas WHERE códcliente = ?", (codigo,)
        ).fetchall()
        if not pieza_ids:
            return {"cliente_id": codigo, "piezas_actualizadas": 0, "pide_probeta_traccion": activar}
        valor = 1 if activar else 0
        conn.executemany("""
            INSERT INTO _piezas_config (pieza_id, pide_probeta_traccion)
            VALUES (?, ?)
            ON CONFLICT(pieza_id) DO UPDATE SET pide_probeta_traccion = excluded.pide_probeta_traccion
        """, [(r["id"], valor) for r in pieza_ids])
        conn.commit()
        return {"cliente_id": codigo, "piezas_actualizadas": len(pieza_ids), "pide_probeta_traccion": activar}
    finally:
        conn.close()


@app.get("/api/piezas_config")
def get_piezas_config():
    """Lista todas las piezas con configuración especial."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT pc.pieza_id, np.nombrepieza,
                   COALESCE(c.nombrefantasía, c.nombrecliente) AS cliente_nombre,
                   pc.pide_probeta_traccion
            FROM _piezas_config pc
            JOIN NombreDePiezas np ON np.id = pc.pieza_id
            JOIN Clientes c ON c.códigocliente = np.códcliente
            ORDER BY np.nombrepieza
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/kiosk/config")
def get_kiosk_config():
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM _kiosk_config WHERE key = 'config'").fetchone()
        default = {"interval": 60, "enabled": ["dash_vencidos","dash_proximos","dash_entregados","rechazos","evol_bar","evol_pct","evol_prod","ranking_rd","ranking_rep"]}
        return json.loads(row["value"]) if row else default
    finally:
        conn.close()


@app.put("/api/kiosk/config")
async def put_kiosk_config(request: Request):
    body = await request.json()
    conn = get_db()
    try:
        conn.execute("INSERT OR REPLACE INTO _kiosk_config (key, value) VALUES ('config', ?)", (json.dumps(body),))
        conn.commit()
        return body
    finally:
        conn.close()


def _calc_tendencia_anual(conn: sqlite3.Connection, año_desde: int, año_hasta: int) -> list[dict]:
    # Delivery trend via Pedidos.fechapedido (fechacargaot is NULL on ~85% of rows)
    trend_rows = conn.execute("""
        SELECT strftime('%Y', p.fechapedido) as año,
               COALESCE(SUM(t.cantidadentregada), 0) as entregadas,
               COALESCE(SUM(t.cantidadrechazada), 0) as rechazadas,
               ROUND(SUM(CASE WHEN np.pesoestablecido > 0 THEN t.cantidadentregada * np.pesoestablecido ELSE 0 END), 1) as kg_entregadas,
               ROUND(SUM(CASE WHEN np.pesoestablecido > 0 THEN t.cantidadrechazada * np.pesoestablecido ELSE 0 END), 1) as kg_rechazadas
        FROM Trabajos t
        JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido
        JOIN Pedidos p ON idp.idpedido = p.idpedido
        LEFT JOIN NombreDePiezas np ON idp.idpieza = np.id
        WHERE p.fechapedido >= ? AND p.fechapedido < ? AND t.cantidadentregada > 0
        GROUP BY año ORDER BY año
    """, (f"{año_desde}-01-01", f"{año_hasta + 1}-01-01")).fetchall()

    # Returns by year (with kg via PesosDePiezas → NombreDePiezas)
    dev_rows = conn.execute("""
        SELECT CAST(id.año AS TEXT) as año,
               COALESCE(SUM(id."cantidaddevolución"), 0) as devueltas,
               ROUND(SUM(CASE WHEN np.pesoestablecido > 0 THEN id."cantidaddevolución" * np.pesoestablecido ELSE 0 END), 1) as kg_devueltas
        FROM "ItemDevolución" id
        JOIN PesosDePiezas pp ON id.códpieza = pp.códpieza
        JOIN NombreDePiezas np ON pp.nombredepiezasid_ = np.id
        WHERE id.año >= ? AND id.año <= ?
        GROUP BY id.año ORDER BY id.año
    """, (año_desde, año_hasta)).fetchall()
    dev_map = {r["año"]: dict(r) for r in dev_rows}

    tendencia = []
    for r in trend_rows:
        dv = dev_map.get(r["año"], {})
        entregadas = r["entregadas"]
        devueltas = dv.get("devueltas", 0)
        kg_entregadas = r["kg_entregadas"] or 0.0
        kg_devueltas = dv.get("kg_devueltas", 0.0)
        tendencia.append({
            "año": r["año"],
            "entregadas": entregadas,
            "rechazadas": r["rechazadas"],
            "devueltas": devueltas,
            "neta": entregadas - devueltas,
            "kg_entregadas": kg_entregadas,
            "kg_rechazadas": r["kg_rechazadas"] or 0.0,
            "kg_devueltas":  kg_devueltas,
            "kg_neta": round(kg_entregadas - kg_devueltas, 1),
        })
    return tendencia


def _calc_tendencia_mensual(conn: sqlite3.Connection, año_desde: int, año_hasta: int) -> list[dict]:
    rows = conn.execute("""
        SELECT strftime('%Y', p.fechapedido) AS año,
               CAST(strftime('%m', p.fechapedido) AS INTEGER) AS mes,
               COALESCE(SUM(t.cantidadentregada), 0) AS entregadas,
               COALESCE(SUM(t.cantidadrechazada), 0) AS rechazadas,
               ROUND(SUM(CASE WHEN np.pesoestablecido > 0 THEN t.cantidadentregada * np.pesoestablecido ELSE 0 END), 1) AS kg_entregadas,
               ROUND(SUM(CASE WHEN np.pesoestablecido > 0 THEN t.cantidadrechazada * np.pesoestablecido ELSE 0 END), 1) AS kg_rechazadas
        FROM Trabajos t
        JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido
        JOIN Pedidos p ON idp.idpedido = p.idpedido
        LEFT JOIN NombreDePiezas np ON idp.idpieza = np.id
        WHERE p.fechapedido >= ? AND p.fechapedido < ? AND t.cantidadentregada > 0
        GROUP BY año, mes ORDER BY año, mes
    """, (f"{año_desde}-01-01", f"{año_hasta + 1}-01-01")).fetchall()
    return [dict(r) for r in rows]


def _año_range_defaults(desde: Optional[int], hasta: Optional[int]) -> tuple[int, int]:
    año_actual = datetime.now().year
    hasta = hasta if hasta is not None else año_actual
    desde = desde if desde is not None else hasta - 4
    if desde > hasta:
        desde, hasta = hasta, desde
    return desde, hasta


@app.get("/api/analytics/overview")
def analytics_overview(desde: Optional[int] = Query(None), hasta: Optional[int] = Query(None)):
    conn = get_db()
    try:
        año_actual = datetime.now().year
        rango_desde, rango_hasta = _año_range_defaults(desde, hasta)
        tendencia = _calc_tendencia_anual(conn, rango_desde, rango_hasta)

        # KPIs del año en curso: independientes del rango elegido para el gráfico de tendencia
        año_ent = año_dev = 0
        for r in _calc_tendencia_anual(conn, año_actual, año_actual):
            año_ent = r["entregadas"]
            año_dev = r["devueltas"]

        # Top 10 clients (last 2 years) — via ItemDetallePedido join
        top = conn.execute("""
            SELECT p.códigocliente as codigo,
                   c.nombrecliente as nombre,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as fantasia,
                   COALESCE(SUM(t.cantidadentregada), 0) as entregadas
            FROM Trabajos t
            JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido
            JOIN Pedidos p ON idp.idpedido = p.idpedido
            JOIN Clientes c ON p.códigocliente = c.códigocliente
            WHERE p.fechapedido >= ? AND t.cantidadentregada > 0
            GROUP BY p.códigocliente
            ORDER BY entregadas DESC
            LIMIT 10
        """, (f"{año_actual - 1}-01-01",)).fetchall()

        n_tablas = len(list_user_tables(conn))
        tasa = round(año_dev / año_ent * 100, 2) if año_ent else 0

        return {
            "año_actual": año_actual,
            "entregadas_año": año_ent,
            "devueltas_año": año_dev,
            "tasa_devolucion": tasa,
            "tendencia": tendencia,
            "tendencia_desde": rango_desde,
            "tendencia_hasta": rango_hasta,
            "top_clientes": [dict(r) for r in top],
            "n_tablas": n_tablas,
        }
    finally:
        conn.close()


@app.get("/api/analytics/tendencia_mensual")
def analytics_tendencia_mensual(desde: Optional[int] = Query(None), hasta: Optional[int] = Query(None)):
    conn = get_db()
    try:
        rango_desde, rango_hasta = _año_range_defaults(desde, hasta)
        return _calc_tendencia_mensual(conn, rango_desde, rango_hasta)
    finally:
        conn.close()


@app.get("/api/analytics/tendencia/anos")
def analytics_tendencia_anos():
    conn = get_db()
    try:
        año_actual = datetime.now().year
        rows = conn.execute("""
            SELECT DISTINCT CAST(strftime('%Y', fechapedido) AS INTEGER) as y
            FROM Pedidos
            WHERE fechapedido IS NOT NULL
              AND CAST(strftime('%Y', fechapedido) AS INTEGER) BETWEEN 1990 AND ?
            ORDER BY y
        """, (año_actual,)).fetchall()
        return {"años": [r["y"] for r in rows], "año_actual": año_actual}
    finally:
        conn.close()


_MESES_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
             "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


@app.get("/api/analytics/tendencia/export")
def analytics_tendencia_export(
    modo: str = Query("anual", pattern="^(anual|mensual)$"),
    desde: Optional[int] = Query(None),
    hasta: Optional[int] = Query(None),
):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    import io

    conn = get_db()
    try:
        rango_desde, rango_hasta = _año_range_defaults(desde, hasta)
        rango_txt = str(rango_desde) if rango_desde == rango_hasta else f"{rango_desde}-{rango_hasta}"
        wb = Workbook()
        ws = wb.active

        if modo == "mensual":
            ws.title = "Tendencia mensual"
            ws.append(["Año", "Mes", "Entregadas", "Rechazadas", "Kg entregadas", "Kg rechazadas"])
            for r in _calc_tendencia_mensual(conn, rango_desde, rango_hasta):
                ws.append([
                    int(r["año"]), _MESES_ES[r["mes"] - 1],
                    r["entregadas"], r["rechazadas"],
                    r["kg_entregadas"] or 0.0, r["kg_rechazadas"] or 0.0,
                ])
            filename = f"tendencia_mensual_{rango_txt}.xlsx"
        else:
            ws.title = "Tendencia anual"
            ws.append(["Año", "Entregadas", "Rechazadas", "Devueltas", "Entregadas - Devueltas",
                       "Kg entregadas", "Kg rechazadas", "Kg devueltas", "Kg entregadas - Kg devueltas"])
            for r in _calc_tendencia_anual(conn, rango_desde, rango_hasta):
                ws.append([
                    int(r["año"]), r["entregadas"], r["rechazadas"], r["devueltas"], r["neta"],
                    r["kg_entregadas"], r["kg_rechazadas"], r["kg_devueltas"], r["kg_neta"],
                ])
            filename = f"tendencia_anual_{rango_txt}.xlsx"

        for cell in ws[1]:
            cell.font = Font(bold=True)
        for col_cells in ws.columns:
            width = max((len(str(c.value)) for c in col_cells if c.value is not None), default=10)
            ws.column_dimensions[col_cells[0].column_letter].width = max(10, width + 2)

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        conn.close()


@app.get("/api/analytics/clientes")
def analytics_clientes(search: Optional[str] = Query(None)):
    conn = get_db()
    try:
        where = ""
        params: list = []
        if search and search.strip():
            like = f"%{search.strip()}%"
            where = " WHERE c.nombrecliente LIKE ? OR c.nombrefantasía LIKE ? OR c.códigocliente LIKE ?"
            params = [like, like, like]
        rows = conn.execute(f"""
            SELECT c.códigocliente as codigo,
                   c.nombrecliente as nombre,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as fantasia,
                   COUNT(np.id) as n_piezas
            FROM Clientes c
            LEFT JOIN NombreDePiezas np ON np.códcliente = c.códigocliente
            {where}
            GROUP BY c.códigocliente
            ORDER BY c.nombrecliente
        """, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/analytics/mejores_clientes")
def analytics_mejores_clientes(
    date_from: Optional[str] = Query(default=None),
    date_to:   Optional[str] = Query(default=None),
):
    conn = get_db()
    try:
        date_filter = ""
        params: list = []
        if date_from:
            date_filter += " AND p.fechapedido >= ?"
            params.append(date_from)
        if date_to:
            date_filter += " AND p.fechapedido <= ?"
            params.append(date_to)
        rows = conn.execute(f"""
            SELECT
                c.códigocliente                                         AS codigo,
                COALESCE(c.nombrefantasía, c.nombrecliente)            AS nombre,
                COUNT(DISTINCT p.idpedido)                             AS n_pedidos,
                date(MAX(p.fechapedido))                               AS ultimo_pedido,
                COALESCE(SUM(t.cantidadentregada), 0)                  AS tot_entregada,
                ROUND(COALESCE(SUM(
                    CASE WHEN np.pesoestablecido IS NOT NULL AND np.pesoestablecido > 0
                         THEN t.cantidadentregada * np.pesoestablecido
                         ELSE 0 END
                ), 0), 1)                                              AS tot_kg
            FROM Clientes c
            JOIN Pedidos p   ON p.códigocliente   = c.códigocliente
            JOIN ItemDetallePedido idp ON idp.idpedido = p.idpedido
            JOIN NombreDePiezas np ON np.id = idp.idpieza
            JOIN Trabajos t  ON t.iditempedido    = idp.iditempedido
            WHERE t.cantidadentregada > 0{date_filter}
            GROUP BY c.códigocliente
            HAVING tot_entregada > 0
            ORDER BY tot_entregada DESC
        """, params).fetchall()
        data = [dict(r) for r in rows]
        grand_total    = sum(r["tot_entregada"] for r in data) or 1

        grand_total_kg = sum(r["tot_kg"]        for r in data) or 1
        for r in data:
            r["pct"]    = round(r["tot_entregada"] / grand_total    * 100, 2)
            r["pct_kg"] = round(r["tot_kg"]        / grand_total_kg * 100, 2)
        return data
    finally:
        conn.close()


@app.get("/api/analytics/rechazos")
def analytics_rechazos(
    date_from: Optional[str] = Query(default=None),
    date_to:   Optional[str] = Query(default=None),
):
    conn = get_db()
    try:
        date_filter = ""
        params: list = []
        if date_from:
            date_filter += " AND p.fechapedido >= ?"
            params.append(date_from)
        if date_to:
            date_filter += " AND p.fechapedido <= ?"
            params.append(date_to)

        piece_rows = conn.execute(f"""
            SELECT
                c.códigocliente                                AS codigo,
                COALESCE(c.nombrefantasía, c.nombrecliente)   AS nombre,
                np.id                                          AS idpieza,
                np.nombrepieza                                 AS pieza_nombre,
                np.códigopiezapuestoporcliente                 AS pieza_codigo,
                COALESCE(SUM(t.cantidadrechazada), 0)          AS tot_rechazada,
                COALESCE(SUM(t.cantidadproducida),  0)         AS tot_producida,
                COALESCE(SUM(t.cantidadentregada),  0)         AS tot_entregada
            FROM Clientes c
            JOIN Pedidos p          ON p.códigocliente   = c.códigocliente
            JOIN ItemDetallePedido idp ON idp.idpedido   = p.idpedido
            JOIN NombreDePiezas np  ON np.id             = idp.idpieza
            JOIN Trabajos t         ON t.iditempedido    = idp.iditempedido
            WHERE t.cantidadrechazada > 0{date_filter}
            GROUP BY c.códigocliente, np.id
            HAVING tot_rechazada > 0
            ORDER BY codigo, tot_rechazada DESC
        """, params).fetchall()

        fallo_rows_rech = conn.execute(f"""
            SELECT
                c.códigocliente  AS codigo,
                idp.idpieza      AS idpieza,
                sri.descripción  AS descripcion,
                COUNT(*)         AS ocurrencias,
                MAX(substr(t.fechacargaot, 1, 10)) AS ultima_fecha
            FROM SoluciónRechazoInterno sri
            JOIN Trabajos t         ON t.iditemtrabajo   = sri.iditemtrabajo
            JOIN ItemDetallePedido idp ON idp.iditempedido = t.iditempedido
            JOIN Pedidos p          ON p.idpedido        = idp.idpedido
            JOIN Clientes c         ON c.códigocliente   = p.códigocliente
            WHERE sri.descripción IS NOT NULL
              AND TRIM(sri.descripción) NOT IN ('', '--'){date_filter}
            GROUP BY c.códigocliente, idp.idpieza, sri.descripción
            ORDER BY codigo, idpieza, ocurrencias DESC
        """, params).fetchall()

        clients: dict = {}
        for r in piece_rows:
            cod = r["codigo"]
            if cod not in clients:
                clients[cod] = {"codigo": cod, "nombre": r["nombre"],
                                "tot_rechazada": 0, "tot_producida": 0, "tot_entregada": 0, "piezas": {}}
            c = clients[cod]
            c["tot_rechazada"] += r["tot_rechazada"]
            c["tot_producida"]  += r["tot_producida"]
            c["tot_entregada"]  += r["tot_entregada"]
            c["piezas"][r["idpieza"]] = {
                "idpieza":      r["idpieza"],
                "nombre":       r["pieza_nombre"],
                "codigo":       r["pieza_codigo"],
                "tot_rechazada": r["tot_rechazada"],
                "tot_producida": r["tot_producida"],
                "tot_entregada": r["tot_entregada"],
                "fallos": [],
            }

        for r in fallo_rows_rech:
            cod, pid = r["codigo"], r["idpieza"]
            if cod in clients and pid in clients[cod]["piezas"]:
                clients[cod]["piezas"][pid]["fallos"].append(
                    {"descripcion": r["descripcion"], "ocurrencias": r["ocurrencias"],
                     "ultima_fecha": r["ultima_fecha"]}
                )

        result = []
        for c in clients.values():
            c["pct_rechazo"] = round(
                c["tot_rechazada"] / c["tot_producida"] * 100, 2
            ) if c["tot_producida"] else 0
            piezas = sorted(c["piezas"].values(), key=lambda x: x["tot_rechazada"], reverse=True)
            for p in piezas:
                p["pct_of_client"] = round(
                    p["tot_rechazada"] / c["tot_rechazada"] * 100, 1
                ) if c["tot_rechazada"] else 0
                p["pct_rechazo"] = round(
                    p["tot_rechazada"] / p["tot_producida"] * 100, 1
                ) if p["tot_producida"] else 0
                tot_g = sum(g["ocurrencias"] for g in p["fallos"])
                for g in p["fallos"]:
                    g["pct"] = round(g["ocurrencias"] / tot_g * 100, 1) if tot_g else 0
            c["piezas"] = piezas
            result.append(c)

        result.sort(key=lambda x: x["tot_rechazada"], reverse=True)
        return result
    finally:
        conn.close()


@app.get("/api/analytics/rechazos_moldeador")
def analytics_rechazos_moldeador(anio_desde: int = Query(default=2024)):
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT r.códigoresponsable as cod,
                   r.apellidoynombreresponsable as nombre,
                   SUM(t.cantidadrechazada) as rechazadas,
                   SUM(t.cantidadreparada)  as reparadas,
                   SUM(t.cantidadproducida) as producidas
            FROM Trabajos t
            JOIN "FundiciónPorFecha" fpf ON fpf.códfundición = t.códfundición
            JOIN Responsables r ON r.códigoresponsable = t.códigoresponsable1
            WHERE fpf.fecha >= ?
              AND t.códfundición IS NOT NULL
              AND t.códigoresponsable1 NOT IN (-90,-91,-92,-93,-94,300)
              AND (r.códsector < 2 OR r.códsector = 6)
            GROUP BY r.códigoresponsable
            HAVING rechazadas > 0
            ORDER BY rechazadas DESC
        """, (f"{anio_desde}-01-01",)).fetchall()
        result = []
        for r in rows:
            prod = r["producidas"] or 0
            rech = r["rechazadas"] or 0
            rep  = r["reparadas"]  or 0
            result.append({
                "cod":        r["cod"],
                "nombre":     r["nombre"],
                "producidas": prod,
                "rechazadas": rech,
                "reparadas":  rep,
                "pct_rechazo":   round(rech / prod * 100, 2) if prod else 0,
                "pct_reparacion": round(rep / prod * 100, 2) if prod else 0,
            })
        return result
    finally:
        conn.close()


@app.get("/api/analytics/devoluciones")
def analytics_devoluciones(
    date_from: Optional[str] = Query(default=None),
    date_to:   Optional[str] = Query(default=None),
):
    conn = get_db()
    try:
        # Filtro para ItemDevolución: usar (año*100 + semana) para precisión semanal.
        # Evita incluir semanas del mismo año que caen fuera del rango pedido.
        def _iso_week_key(date_str: str) -> int:
            d = date_cls.fromisoformat(date_str[:10])
            iso_year, iso_week, _ = d.isocalendar()
            return iso_year * 100 + iso_week

        yf = ""
        params_p: list = []
        params_f: list = []
        if date_from:
            yf += " AND (id.año * 100 + id.semana) >= ?"
            params_p.append(_iso_week_key(date_from))
            params_f.append(_iso_week_key(date_from))
        if date_to:
            yf += " AND (id.año * 100 + id.semana) <= ?"
            params_p.append(_iso_week_key(date_to))
            params_f.append(_iso_week_key(date_to))

        # Usar códpieza → PesosDePiezas → NombreDePiezas (cobertura 100%).
        # SoluciónDevolución solo cubre ~80-90% de los registros; los más recientes
        # aún no tienen SD cargada en Access, por lo que se pierden con el JOIN anterior.
        piece_rows = conn.execute(f"""
            SELECT
                np.códcliente                                          AS codigo,
                COALESCE(c.nombrefantasía, c.nombrecliente)            AS nombre,
                np.id                                                  AS idpieza,
                np.nombrepieza                                         AS pieza_nombre,
                np.códigopiezapuestoporcliente                         AS pieza_codigo,
                COALESCE(SUM(id."cantidaddevolución"), 0)              AS tot_devuelta
            FROM "ItemDevolución" id
            JOIN PesosDePiezas pp  ON pp.códpieza = id.códpieza
            JOIN NombreDePiezas np ON np.id = pp.nombredepiezasid_
            JOIN Clientes c        ON c.códigocliente = np.códcliente
            WHERE 1=1{yf}
            GROUP BY np.códcliente, np.id
            HAVING tot_devuelta > 0
            ORDER BY codigo, tot_devuelta DESC
        """, params_p).fetchall()

        # Fallos: SoluciónDevolución sigue siendo la fuente de descripciones,
        # pero la pieza se identifica via códpieza para consistencia con piece_rows.
        fallo_rows = conn.execute(f"""
            SELECT
                np.códcliente                 AS codigo,
                np.id                         AS idpieza,
                sd.descripción                AS descripcion,
                COUNT(*)                      AS ocurrencias,
                MAX(id.año * 100 + id.semana) AS ultimo_yw
            FROM "SoluciónDevolución" sd
            JOIN "ItemDevolución" id ON id.iddevol = sd.iddevol
            JOIN PesosDePiezas pp  ON pp.códpieza = id.códpieza
            JOIN NombreDePiezas np ON np.id = pp.nombredepiezasid_
            WHERE sd.descripción IS NOT NULL
              AND TRIM(sd.descripción) NOT IN ('', '--'){yf}
            GROUP BY np.códcliente, np.id, sd.descripción
            ORDER BY codigo, idpieza, ocurrencias DESC
        """, params_f).fetchall()

        # Entregadas por cliente+pieza: usar fechas exactas igual que rechazos
        ent_yf = ""
        ent_params: list = []
        if date_from:
            ent_yf += " AND p.fechapedido >= ?"
            ent_params.append(date_from)
        if date_to:
            ent_yf += " AND p.fechapedido <= ?"
            ent_params.append(date_to)

        ent_rows = conn.execute(f"""
            SELECT np.códcliente AS codigo, np.id AS idpieza,
                   COALESCE(SUM(t.cantidadentregada), 0) AS tot_entregada
            FROM Trabajos t
            JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido
            JOIN Pedidos p             ON idp.idpedido   = p.idpedido
            JOIN NombreDePiezas np     ON idp.idpieza    = np.id
            WHERE t.cantidadentregada > 0{ent_yf}
            GROUP BY np.códcliente, np.id
        """, ent_params).fetchall()
        ent_map = {(r["codigo"], r["idpieza"]): r["tot_entregada"] for r in ent_rows}

        clients: dict = {}
        for r in piece_rows:
            cod = r["codigo"]
            if cod not in clients:
                clients[cod] = {"codigo": cod, "nombre": r["nombre"],
                                "tot_devuelta": 0, "tot_entregada": 0, "piezas": {}}
            c = clients[cod]
            ent = ent_map.get((cod, r["idpieza"]), 0)
            c["tot_devuelta"]  += r["tot_devuelta"]
            c["tot_entregada"] += ent
            c["piezas"][r["idpieza"]] = {
                "idpieza":      r["idpieza"],
                "nombre":       r["pieza_nombre"],
                "codigo":       r["pieza_codigo"],
                "tot_devuelta": r["tot_devuelta"],
                "tot_entregada": ent,
                "fallos": [],
            }

        for r in fallo_rows:
            cod, pid = r["codigo"], r["idpieza"]
            if cod in clients and pid in clients[cod]["piezas"]:
                yw = r["ultimo_yw"] or 0
                clients[cod]["piezas"][pid]["fallos"].append(
                    {"descripcion": r["descripcion"], "ocurrencias": r["ocurrencias"],
                     "ultima_semana": yw % 100, "ultimo_año": yw // 100}
                )

        result = []
        for c in clients.values():
            c["pct_devolucion"] = round(
                c["tot_devuelta"] / c["tot_entregada"] * 100, 2
            ) if c["tot_entregada"] else 0
            piezas = sorted(c["piezas"].values(), key=lambda x: x["tot_devuelta"], reverse=True)
            for p in piezas:
                p["pct_of_client"] = round(
                    p["tot_devuelta"] / c["tot_devuelta"] * 100, 1
                ) if c["tot_devuelta"] else 0
                p["pct_devolucion"] = round(
                    p["tot_devuelta"] / p["tot_entregada"] * 100, 1
                ) if p["tot_entregada"] else 0
                tot_f = sum(f["ocurrencias"] for f in p["fallos"])
                for f in p["fallos"]:
                    f["pct"] = round(f["ocurrencias"] / tot_f * 100, 1) if tot_f else 0
            c["piezas"] = piezas
            result.append(c)

        result.sort(key=lambda x: x["tot_devuelta"], reverse=True)
        return result
    finally:
        conn.close()


@app.get("/api/analytics/devoluciones_pendientes")
def analytics_devoluciones_pendientes():
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT id.iddevol,
                   id.año,
                   id.semana,
                   id."cantidaddevolución" as cantidad,
                   np.nombrepieza as nombre_pieza,
                   np.códigopiezapuestoporcliente as codigo_pieza,
                   np.códcliente as cliente_cod,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as cliente_nombre
            FROM "ItemDevolución" id
            JOIN PesosDePiezas pp  ON pp.códpieza = id.códpieza
            JOIN NombreDePiezas np ON np.id = pp.nombredepiezasid_
            LEFT JOIN Clientes c   ON c.códigocliente = np.códcliente
            LEFT JOIN "SoluciónDevolución" sd ON sd.iddevol = id.iddevol
            WHERE sd.iddevol IS NULL
              AND id."cantidaddevolución" > 0
            ORDER BY id.año DESC, id.semana DESC
        """).fetchall()
        return [
            {**dict(r), "fecha_rango": _iso_week_label(r["año"], r["semana"])}
            for r in rows
        ]
    finally:
        conn.close()


@app.get("/api/analytics/entregas_devol")
def analytics_entregas_devol(
    cliente:   str            = Query(...),
    date_from: Optional[str]  = Query(None),
    date_to:   Optional[str]  = Query(None),
    piezas:    List[int]      = Query(default=[]),
):
    conn = get_db()
    try:
        cl = conn.execute("""
            SELECT códigocliente as codigo,
                   COALESCE(nombrefantasía, nombrecliente) as nombre
            FROM Clientes WHERE códigocliente = ?
        """, (cliente,)).fetchone()
        if not cl:
            raise HTTPException(404, f"Cliente '{cliente}' no encontrado")

        # ── Entregas via ItemDetalle + Remitos ─────────────────────────────
        date_f: str = ""
        date_p: list = []
        if date_from:
            date_f += " AND substr(r.fecharemito,1,10) >= ?"
            date_p.append(date_from[:10])
        if date_to:
            date_f += " AND substr(r.fecharemito,1,10) <= ?"
            date_p.append(date_to[:10])
        pieza_f_ent: str = ""
        if piezas:
            ph = ",".join("?" * len(piezas))
            pieza_f_ent = f" AND pp.nombredepiezasid_ IN ({ph})"
            date_p = date_p + list(piezas)

        ent_rows = conn.execute(f"""
            SELECT
                COALESCE(np.códigopiezapuestoporcliente,
                         CAST(id.códpieza AS TEXT))        AS codigo,
                COALESCE(np.nombrepieza,
                         CAST(id.códpieza AS TEXT))        AS nombre,
                r.idnroremito                              AS remito,
                id.cantidad                               AS cant,
                p.nropedido                               AS oc,
                substr(r.fecharemito, 1, 10)              AS fecha
            FROM ItemDetalle id
            JOIN Remitos r ON r.idnroremito = id.idnroremito
            LEFT JOIN PesosDePiezas pp  ON pp.códpieza         = id.códpieza
            LEFT JOIN NombreDePiezas np ON np.id               = pp.nombredepiezasid_
            LEFT JOIN Trabajos t        ON t.iditemtrabajo      = id.iditemtrabajo
            LEFT JOIN ItemDetallePedido idp ON idp.iditempedido = t.iditempedido
            LEFT JOIN Pedidos p         ON p.idpedido           = idp.idpedido
            WHERE r.códigocliente = ?
              AND (r.marcaborrar IS NULL OR r.marcaborrar = 'False')
              AND id.cantidad > 0{date_f}{pieza_f_ent}
            ORDER BY r.fecharemito, r.idnroremito, COALESCE(np.nombrepieza, '')
        """, [cliente] + date_p).fetchall()

        # ── Devoluciones via ItemDevolución → PesosDePiezas ────────────────
        def _iso_wk(ds: str) -> int:
            from datetime import date as _d
            d = _d.fromisoformat(ds[:10])
            y, w, _ = d.isocalendar()
            return y * 100 + w

        wf: str = ""
        wp: list = []
        if date_from:
            wf += " AND (id.año * 100 + id.semana) >= ?"
            wp.append(_iso_wk(date_from))
        if date_to:
            wf += " AND (id.año * 100 + id.semana) <= ?"
            wp.append(_iso_wk(date_to))
        if piezas:
            ph = ",".join("?" * len(piezas))
            wf += f" AND np.id IN ({ph})"
            wp = wp + list(piezas)

        dev_rows = conn.execute(f"""
            SELECT
                np.códigopiezapuestoporcliente  AS codigo,
                np.nombrepieza                  AS nombre,
                id."cantidaddevolución"         AS cant,
                id.iddevol                      AS doc,
                id.año                          AS anio,
                id.semana                       AS semana
            FROM "ItemDevolución" id
            JOIN PesosDePiezas pp  ON pp.códpieza = id.códpieza
            JOIN NombreDePiezas np ON np.id        = pp.nombredepiezasid_
            WHERE np.códcliente = ?{wf}
              AND id."cantidaddevolución" > 0
            ORDER BY id.año, id.semana, np.nombrepieza
        """, [cliente] + wp).fetchall()

        def _wk_date(anio, semana) -> str | None:
            try:
                from datetime import datetime as _dt
                return _dt.fromisocalendar(int(anio), int(semana), 1).strftime("%Y-%m-%d")
            except Exception:
                return None

        total_ent = sum((r["cant"] or 0) for r in ent_rows)
        total_dev = sum((r["cant"] or 0) for r in dev_rows)
        pct = round(total_dev / total_ent * 100, 2) if total_ent else 0

        return {
            "cliente": dict(cl),
            "entregas": [
                {
                    "codigo": r["codigo"],
                    "nombre": r["nombre"],
                    "remito": r["remito"],
                    "cant":   r["cant"],
                    "oc":     r["oc"],
                    "fecha":  r["fecha"],
                }
                for r in ent_rows
            ],
            "devoluciones": [
                {
                    "codigo": r["codigo"],
                    "nombre": r["nombre"],
                    "cant":   r["cant"],
                    "doc":    r["doc"],
                    "semana": _iso_week_label(r["anio"], r["semana"]),
                    "fecha":  _wk_date(r["anio"], r["semana"]),
                }
                for r in dev_rows
            ],
            "totales": {
                "entregas":     total_ent,
                "devoluciones": total_dev,
                "pct":          pct,
            },
        }
    finally:
        conn.close()


@app.get("/api/analytics/tendencia_piezas")
def analytics_tendencia_piezas(ids: List[int] = Query(default=[])):
    if not ids:
        return {"piezas": [], "anual": []}
    conn = get_db()
    try:
        ph = ",".join("?" * len(ids))

        # Piece names
        piezas_rows = conn.execute(
            f"SELECT np.id, np.nombrepieza as nombre, "
            f"np.códigopiezapuestoporcliente as codigo, "
            f"COALESCE(c.nombrefantasía, c.nombrecliente) as cliente, "
            f"np.pesoestablecido as peso "
            f"FROM NombreDePiezas np "
            f"LEFT JOIN Clientes c ON c.códigocliente = np.códcliente "
            f"WHERE np.id IN ({ph})",
            ids,
        ).fetchall()

        # Annual rechazos & entregas from Trabajos (with kg via pesoestablecido)
        ent_rows = conn.execute(
            f"SELECT strftime('%Y', p.fechapedido) as año, "
            f"COALESCE(SUM(t.cantidadproducida), 0) as producidas, "
            f"COALESCE(SUM(t.cantidadrechazada), 0) as rechazadas, "
            f"COALESCE(SUM(t.cantidadentregada), 0) as entregadas, "
            f"ROUND(SUM(CASE WHEN np.pesoestablecido > 0 THEN t.cantidadentregada * np.pesoestablecido ELSE 0 END), 1) as kg_entregadas, "
            f"ROUND(SUM(CASE WHEN np.pesoestablecido > 0 THEN t.cantidadrechazada * np.pesoestablecido ELSE 0 END), 1) as kg_rechazadas, "
            f"ROUND(SUM(CASE WHEN np.pesoestablecido > 0 THEN t.cantidadproducida * np.pesoestablecido ELSE 0 END), 1) as kg_producidas "
            f"FROM Trabajos t "
            f"JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido "
            f"JOIN Pedidos p ON idp.idpedido = p.idpedido "
            f"LEFT JOIN NombreDePiezas np ON idp.idpieza = np.id "
            f"WHERE idp.idpieza IN ({ph}) AND p.fechapedido IS NOT NULL "
            f"GROUP BY año ORDER BY año",
            ids,
        ).fetchall()

        # Annual devoluciones from ItemDevolución via PesosDePiezas (with kg)
        dev_rows = conn.execute(
            f"SELECT CAST(id.año AS TEXT) as año, "
            f"COALESCE(SUM(id.\"cantidaddevolución\"), 0) as devueltas, "
            f"ROUND(SUM(CASE WHEN np.pesoestablecido > 0 THEN id.\"cantidaddevolución\" * np.pesoestablecido ELSE 0 END), 1) as kg_devueltas "
            f"FROM \"ItemDevolución\" id "
            f"JOIN PesosDePiezas pp ON id.códpieza = pp.códpieza "
            f"JOIN NombreDePiezas np ON pp.nombredepiezasid_ = np.id "
            f"WHERE pp.nombredepiezasid_ IN ({ph}) "
            f"GROUP BY id.año ORDER BY id.año",
            ids,
        ).fetchall()
        dev_map = {r["año"]: dict(r) for r in dev_rows}
        ent_map = {r["año"]: r for r in ent_rows}

        all_years = sorted(set(ent_map) | set(dev_map))
        anual = []
        for año in all_years:
            er = ent_map.get(año)
            dv = dev_map.get(año, {})
            dev = dv.get("devueltas", 0)
            prod = er["producidas"] if er else 0
            rech = er["rechazadas"] if er else 0
            ent  = er["entregadas"] if er else 0
            anual.append({
                "año": año,
                "producidas": prod,
                "rechazadas": rech,
                "entregadas": ent,
                "devueltas":  dev,
                "kg_entregadas": er["kg_entregadas"] if er else 0.0,
                "kg_rechazadas": er["kg_rechazadas"] if er else 0.0,
                "kg_producidas": er["kg_producidas"] if er else 0.0,
                "kg_devueltas":  dv.get("kg_devueltas", 0.0),
                "pct_rechazo":    round(rech / prod * 100, 2) if prod else 0,
                "pct_devolucion": round(dev  / ent  * 100, 2) if ent  else 0,
            })

        mensual_rows = conn.execute(
            f"SELECT strftime('%Y', p.fechapedido) AS año, "
            f"CAST(strftime('%m', p.fechapedido) AS INTEGER) AS mes, "
            f"COALESCE(SUM(t.cantidadentregada), 0) AS entregadas, "
            f"COALESCE(SUM(t.cantidadrechazada), 0) AS rechazadas, "
            f"ROUND(SUM(CASE WHEN np.pesoestablecido > 0 THEN t.cantidadentregada * np.pesoestablecido ELSE 0 END), 1) AS kg_entregadas, "
            f"ROUND(SUM(CASE WHEN np.pesoestablecido > 0 THEN t.cantidadrechazada * np.pesoestablecido ELSE 0 END), 1) AS kg_rechazadas "
            f"FROM Trabajos t "
            f"JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido "
            f"JOIN Pedidos p ON idp.idpedido = p.idpedido "
            f"LEFT JOIN NombreDePiezas np ON idp.idpieza = np.id "
            f"WHERE idp.idpieza IN ({ph}) AND p.fechapedido IS NOT NULL "
            f"GROUP BY año, mes ORDER BY año, mes",
            ids,
        ).fetchall()

        return {
            "piezas": [dict(r) for r in piezas_rows],
            "anual": anual,
            "mensual": [dict(r) for r in mensual_rows],
        }
    finally:
        conn.close()


@app.get("/api/analytics/evolucion_mensual")
def analytics_evolucion_mensual(meses: int = Query(default=24, ge=3, le=60)):
    import datetime as _dt
    conn = get_db()
    try:
        # Count-based monthly aggregation — grouped by production date (FundiciónPorFecha)
        count_rows = conn.execute("""
            SELECT strftime('%Y-%m', fpf.fecha) as mes,
                   COALESCE(SUM(t.cantidadproducida), 0)  as producidas,
                   COALESCE(SUM(t.cantidadrechazada), 0)  as rechazadas,
                   COALESCE(SUM(t.cantidadreparada),  0)  as reparadas,
                   COALESCE(SUM(t.cantidadaprobada),  0)  as aprobadas,
                   COALESCE(SUM(t.cantidadentregada), 0)  as entregadas
            FROM Trabajos t
            JOIN "FundiciónPorFecha" fpf ON fpf.códfundición = t.códfundición
            WHERE fpf.fecha IS NOT NULL
              AND t.códfundición IS NOT NULL
              AND strftime('%Y-%m', fpf.fecha) <= strftime('%Y-%m', 'now')
            GROUP BY mes
            ORDER BY mes DESC
            LIMIT ?
        """, (meses,)).fetchall()

        # Kg-based monthly aggregation — two formulas:
        #   metal: peso histórico OT × coefcompl × AD/TR → total metal consumed (incl. losses/sprues)
        #   pieza: ÚltimoDePesoPieza (peso vigente actual, igual que Excel Power Query)
        kg_rows = conn.execute("""
            WITH peso_vigente AS (
                SELECT nombredepiezasid_,
                       MAX(códpieza) AS max_cod
                FROM PesosDePiezas
                WHERE cuentaasteríscos >= 0
                GROUP BY nombredepiezasid_
            )
            SELECT strftime('%Y-%m', fpf.fecha) as mes,
                   ROUND(SUM(t.cantidadaprobada *
                       CASE WHEN np.nombrepieza LIKE '%(AD)' THEN pp.pesopieza * 2
                            WHEN np.nombrepieza LIKE '%(TR)' THEN pp.pesopieza * 3
                            ELSE pp.pesopieza END * COALESCE(np.coefcompl, 1)), 2) as kg_aprobados,
                   ROUND(SUM(t.cantidadrechazada *
                       CASE WHEN np.nombrepieza LIKE '%(AD)' THEN pp.pesopieza * 2
                            WHEN np.nombrepieza LIKE '%(TR)' THEN pp.pesopieza * 3
                            ELSE pp.pesopieza END * COALESCE(np.coefcompl, 1)), 2) as kg_rechazados,
                   ROUND(SUM(t.cantidadreparada *
                       CASE WHEN np.nombrepieza LIKE '%(AD)' THEN pp.pesopieza * 2
                            WHEN np.nombrepieza LIKE '%(TR)' THEN pp.pesopieza * 3
                            ELSE pp.pesopieza END * COALESCE(np.coefcompl, 1)), 2) as kg_reparados,
                   ROUND(SUM(t.cantidadproducida *
                       CASE WHEN np.nombrepieza LIKE '%(AD)' THEN pp.pesopieza * 2
                            WHEN np.nombrepieza LIKE '%(TR)' THEN pp.pesopieza * 3
                            ELSE pp.pesopieza END * COALESCE(np.coefcompl, 1)), 2) as kg_producidos,
                   ROUND(SUM(t.cantidadaprobada  * COALESCE(ppv.pesopieza, pp.pesopieza)), 2) as kg_aprobados_pieza,
                   ROUND(SUM(t.cantidadrechazada * COALESCE(ppv.pesopieza, pp.pesopieza)), 2) as kg_rechazados_pieza,
                   ROUND(SUM(t.cantidadreparada  * COALESCE(ppv.pesopieza, pp.pesopieza)), 2) as kg_reparados_pieza,
                   ROUND(SUM(t.cantidadproducida * COALESCE(ppv.pesopieza, pp.pesopieza)), 2) as kg_producidos_pieza
            FROM Trabajos t
            JOIN "FundiciónPorFecha" fpf ON fpf.códfundición = t.códfundición
            JOIN PesosDePiezas pp  ON pp.códpieza = t.idpesopieza
            JOIN NombreDePiezas np ON np.id = pp.nombredepiezasid_
            LEFT JOIN peso_vigente pv  ON pv.nombredepiezasid_ = np.id
            LEFT JOIN PesosDePiezas ppv ON ppv.códpieza = pv.max_cod
            WHERE fpf.fecha IS NOT NULL
              AND t.códfundición IS NOT NULL
              AND t.idpesopieza IS NOT NULL
              AND strftime('%Y-%m', fpf.fecha) <= strftime('%Y-%m', 'now')
            GROUP BY mes
            ORDER BY mes DESC
            LIMIT ?
        """, (meses,)).fetchall()

        # Moldeo-sector hours only (CódSector < 2 OR = 6), matching Access productivity calc
        hs_rows = conn.execute("""
            SELECT strftime('%Y-%m', hf.fecha) as mes,
                   SUM(hf.horastrabajadas) as hs_trabajadas
            FROM HorasPorFecha hf
            JOIN Responsables r ON r.códigoresponsable = hf.códigoresponsable
            WHERE hf.fecha IS NOT NULL
              AND (r.códsector < 2 OR r.códsector = 6)
              AND strftime('%Y-%m', hf.fecha) <= strftime('%Y-%m', 'now')
            GROUP BY mes
        """).fetchall()
        hs_map = {r["mes"]: (r["hs_trabajadas"] or 0) for r in hs_rows}

        # Devoluciones: ItemDevolución only has año+semana, no date column.
        # Fetch grouped by (año, semana) and convert to month in Python.
        dev_raw = conn.execute("""
            SELECT id.año, id.semana,
                   COALESCE(SUM(id."cantidaddevolución"), 0) as devueltas,
                   COALESCE(ROUND(SUM(id."cantidaddevolución" *
                       CASE WHEN np.nombrepieza LIKE '%(AD)' THEN pp.pesopieza * 2
                            WHEN np.nombrepieza LIKE '%(TR)' THEN pp.pesopieza * 3
                            ELSE pp.pesopieza END * COALESCE(np.coefcompl, 1)), 2), 0) as kg_devueltos
            FROM "ItemDevolución" id
            LEFT JOIN PesosDePiezas pp ON pp.códpieza = id.códpieza
            LEFT JOIN NombreDePiezas np ON np.id = pp.nombredepiezasid_
            GROUP BY id.año, id.semana
        """).fetchall()

        def _week_to_month(year: int, week: int) -> str:
            jan4 = _dt.date(year, 1, 4)
            week1_mon = jan4 - _dt.timedelta(days=jan4.weekday())
            target = week1_mon + _dt.timedelta(weeks=week - 1)
            return target.strftime("%Y-%m")

        dev_map: dict = {}
        for dv in dev_raw:
            try:
                m = _week_to_month(int(dv["año"]), int(dv["semana"]))
            except Exception:
                continue
            if m not in dev_map:
                dev_map[m] = {"devueltas": 0, "kg_devueltos": 0.0}
            dev_map[m]["devueltas"]   += dv["devueltas"]
            dev_map[m]["kg_devueltos"] += dv["kg_devueltos"] or 0.0

        kg_map = {r["mes"]: r for r in kg_rows}

        meses_data = []
        for r in reversed(count_rows):
            mes  = r["mes"]
            kg   = kg_map.get(mes)
            dv   = dev_map.get(mes, {"devueltas": 0, "kg_devueltos": 0.0})
            hs   = hs_map.get(mes, 0)
            prod = r["producidas"]
            rech = r["rechazadas"]
            rep  = r["reparadas"]
            apro = r["aprobadas"]
            ent  = r["entregadas"]
            devueltas    = dv["devueltas"]
            kg_devueltos = round(dv["kg_devueltos"], 2)
            kg_a   = kg["kg_aprobados"]        if kg else None
            kg_r   = kg["kg_rechazados"]       if kg else None
            kg_rp  = kg["kg_reparados"]        if kg else None
            kg_p   = kg["kg_producidos"]       if kg else None
            kg_a_pz  = kg["kg_aprobados_pieza"]  if kg else None
            kg_r_pz  = kg["kg_rechazados_pieza"] if kg else None
            kg_rp_pz = kg["kg_reparados_pieza"]  if kg else None
            kg_p_pz  = kg["kg_producidos_pieza"] if kg else None
            meses_data.append({
                "mes":            mes,
                "producidas":     prod,
                "rechazadas":     rech,
                "reparadas":      rep,
                "aprobadas":      apro,
                "entregadas":     ent,
                "devueltas":      devueltas,
                "hs_trabajadas":  hs,
                "pct_scrap":      round(rech / prod * 100, 2) if prod else 0,
                "pct_reparacion": round(rep  / apro * 100, 2) if apro else 0,
                "pct_devolucion": round(devueltas / ent * 100, 2) if ent else 0,
                # metal = peso × coefcompl × AD/TR (total metal consumed including losses)
                "kg_aprobados":   kg_a,
                "kg_rechazados":  kg_r,
                "kg_reparados":   kg_rp,
                "kg_producidos":  kg_p,
                "kg_devueltos":   kg_devueltos if (kg_a is not None) else None,
                "pct_scrap_kg":   round(kg_r  / kg_p * 100, 2) if (kg_p and kg_p > 0) else None,
                "pct_rep_kg":     round(kg_rp / kg_a * 100, 2) if (kg_a and kg_a > 0) else None,
                "pct_devol_kg":   round(kg_devueltos / kg_a * 100, 2) if (kg_a and kg_a > 0) else None,
                "productividad":  round(kg_a / hs, 2) if (kg_a and hs and hs > 0) else None,
                "scrap_hs":       round(kg_r  / hs, 2) if (kg_r  is not None and hs and hs > 0) else None,
                "devol_hs":       round(kg_devueltos / hs, 2) if (kg_devueltos is not None and hs and hs > 0) else None,
                # pieza = peso solo (sin coef) → peso físico de la pieza terminada, coincide con Excel
                "kg_aprobados_pieza":   kg_a_pz,
                "kg_rechazados_pieza":  kg_r_pz,
                "kg_reparados_pieza":   kg_rp_pz,
                "kg_producidos_pieza":  kg_p_pz,
                "kg_devueltos_pieza":   kg_devueltos if (kg_a_pz is not None) else None,
                "pct_scrap_kg_pieza":   round(kg_r_pz / kg_p_pz * 100, 2) if (kg_p_pz and kg_p_pz > 0) else None,
                "pct_rep_kg_pieza":     round(kg_rp_pz / kg_a_pz * 100, 2) if (kg_a_pz and kg_a_pz > 0) else None,
                "productividad_pieza":  round(kg_a_pz / hs, 2) if (kg_a_pz and hs and hs > 0) else None,
            })

        return {"meses": meses_data}
    finally:
        conn.close()


@app.get("/api/analytics/semanal")
def analytics_semanal(semanas: int = Query(default=12, ge=4, le=52)):
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT strftime('%Y', fpf.fecha)                      as anio,
                   CAST(strftime('%W', fpf.fecha) AS INTEGER)     as semana,
                   MIN(substr(fpf.fecha, 1, 10))                  as fecha_inicio,
                   COALESCE(SUM(t.cantidadproducida), 0)          as producidas,
                   COALESCE(SUM(t.cantidadrechazada), 0)          as rechazadas,
                   COALESCE(SUM(t.cantidadreparada),  0)          as reparadas,
                   COALESCE(SUM(t.cantidadaprobada),  0)          as aprobadas,
                   ROUND(SUM(t.cantidadaprobada *
                       CASE WHEN np.nombrepieza LIKE '%(AD)' THEN pp.pesopieza * 2
                            WHEN np.nombrepieza LIKE '%(TR)' THEN pp.pesopieza * 3
                            ELSE pp.pesopieza END * COALESCE(np.coefcompl, 1)), 1) as kg_aprobados
            FROM Trabajos t
            JOIN "FundiciónPorFecha" fpf ON fpf.códfundición = t.códfundición
            LEFT JOIN PesosDePiezas pp  ON pp.códpieza = t.idpesopieza
            LEFT JOIN NombreDePiezas np ON np.id = pp.nombredepiezasid_
            WHERE fpf.fecha IS NOT NULL
              AND t.códfundición IS NOT NULL
              AND strftime('%Y-%m', fpf.fecha) <= strftime('%Y-%m', 'now')
            GROUP BY anio, semana
            ORDER BY anio DESC, semana DESC
            LIMIT ?
        """, (semanas,)).fetchall()
        result = []
        for r in reversed(list(rows)):
            prod = r["producidas"] or 0
            rech = r["rechazadas"] or 0
            result.append({
                "anio":         r["anio"],
                "semana":       r["semana"],
                "label":        _sem_label(r["fecha_inicio"]),
                "fecha_inicio": r["fecha_inicio"],
                "producidas":   prod,
                "rechazadas":   rech,
                "reparadas":    r["reparadas"] or 0,
                "aprobadas":    r["aprobadas"] or 0,
                "kg_aprobados": r["kg_aprobados"],
                "pct_scrap":    round(rech / prod * 100, 2) if prod else 0,
            })
        return {"semanas": result}
    finally:
        conn.close()


@app.get("/api/analytics/top_defectos")
def analytics_top_defectos(meses: int = Query(default=6, ge=3, le=12)):
    import datetime as _dt
    conn = get_db()
    try:
        today = _dt.date.today()
        mes_list = []
        for i in range(meses, 0, -1):
            m = today.month - i; y = today.year
            while m <= 0: m += 12; y -= 1
            mes_list.append(f"{y:04d}-{m:02d}")
        mes_set = set(mes_list)

        def _top5_per_month(rows, key):
            from collections import defaultdict
            cnt: dict = defaultdict(lambda: defaultdict(int))
            for r in rows:
                mes = r["mes"]
                if mes not in mes_set: continue
                val = r[key]
                if not val or str(val).strip() in ('', '--', 'None'): continue
                cnt[mes][val.strip()] += 1
            result = []
            for mes in mes_list:
                items = sorted(cnt.get(mes, {}).items(), key=lambda x: -x[1])
                top5 = items[:5]
                otros = sum(v for _, v in items[5:])
                result.append({
                    "mes": mes,
                    "top5": [{"nombre": n, "cant": c} for n, c in top5],
                    "otros": otros,
                })
            return result

        rech_rows = conn.execute("""
            SELECT strftime('%Y-%m', fpf.fecha) as mes,
                   sri."Descripción" as defecto
            FROM "SoluciónRechazoInterno" sri
            JOIN Trabajos t ON t.iditemtrabajo = sri."IdItemTrabajo"
            JOIN "FundiciónPorFecha" fpf ON fpf.códfundición = t.códfundición
            WHERE fpf.fecha IS NOT NULL
              AND t.códfundición IS NOT NULL
              AND strftime('%Y-%m', fpf.fecha) <= strftime('%Y-%m', 'now')
        """).fetchall()

        rep_rows = conn.execute("""
            SELECT strftime('%Y-%m', fpf.fecha) as mes,
                   sri."DescripciónRepar" as reparacion
            FROM "SoluciónReparacionInterna" sri
            JOIN Trabajos t ON t.iditemtrabajo = sri."IdItemTrabajo"
            JOIN "FundiciónPorFecha" fpf ON fpf.códfundición = t.códfundición
            WHERE fpf.fecha IS NOT NULL
              AND t.códfundición IS NOT NULL
              AND strftime('%Y-%m', fpf.fecha) <= strftime('%Y-%m', 'now')
        """).fetchall()

        return {
            "meses": mes_list,
            "defectos":    _top5_per_month(rech_rows, "defecto"),
            "reparaciones": _top5_per_month(rep_rows,  "reparacion"),
        }
    finally:
        conn.close()


@app.get("/api/analytics/top_piezas")
def analytics_top_piezas(meses: int = Query(default=6, ge=3, le=12)):
    import datetime as _dt
    conn = get_db()
    try:
        today = _dt.date.today()
        mes_list = []
        for i in range(meses, 0, -1):
            m = today.month - i
            y = today.year
            while m <= 0:
                m += 12
                y -= 1
            mes_list.append(f"{y:04d}-{m:02d}")

        def _w2m(year, week):
            jan4 = _dt.date(int(year), 1, 4)
            return (jan4 - _dt.timedelta(days=jan4.weekday()) + _dt.timedelta(weeks=int(week)-1)).strftime("%Y-%m")

        # --- rechazadas per pieza per month (grouped by production date) ---
        rech_rows = conn.execute("""
            SELECT strftime('%Y-%m', fpf.fecha) as mes,
                   t.idpesopieza as cod,
                   np.nombrepieza as nombre,
                   ROUND(SUM(t.cantidadrechazada *
                       CASE WHEN np.nombrepieza LIKE '%(AD)' THEN pp.pesopieza * 2
                            WHEN np.nombrepieza LIKE '%(TR)' THEN pp.pesopieza * 3
                            ELSE pp.pesopieza END * COALESCE(np.coefcompl, 1)), 1) as kg,
                   CAST(SUM(t.cantidadrechazada) AS INTEGER) as cant
            FROM Trabajos t
            JOIN "FundiciónPorFecha" fpf ON fpf.códfundición = t.códfundición
            JOIN PesosDePiezas pp ON pp.códpieza = t.idpesopieza
            LEFT JOIN NombreDePiezas np ON np.id = pp.nombredepiezasid_
            WHERE fpf.fecha IS NOT NULL AND t.códfundición IS NOT NULL
              AND t.cantidadrechazada > 0
              AND strftime('%Y-%m', fpf.fecha) <= strftime('%Y-%m', 'now')
            GROUP BY mes, t.idpesopieza
        """).fetchall()

        # --- devueltas per pieza per semana ---
        dev_rows = conn.execute("""
            SELECT id.año, id.semana, id.códpieza as cod,
                   np.nombrepieza as nombre,
                   ROUND(SUM(id."cantidaddevolución" * pp.pesopieza), 1) as kg,
                   CAST(SUM(id."cantidaddevolución") AS INTEGER) as cant
            FROM "ItemDevolución" id
            JOIN PesosDePiezas pp ON pp.códpieza = id.códpieza
            LEFT JOIN NombreDePiezas np ON np.id = pp.nombredepiezasid_
            GROUP BY id.año, id.semana, id.códpieza
        """).fetchall()

        # --- reparadas per pieza per month (grouped by production date) ---
        rep_rows = conn.execute("""
            SELECT strftime('%Y-%m', fpf.fecha) as mes,
                   t.idpesopieza as cod,
                   np.nombrepieza as nombre,
                   ROUND(SUM(t.cantidadreparada *
                       CASE WHEN np.nombrepieza LIKE '%(AD)' THEN pp.pesopieza * 2
                            WHEN np.nombrepieza LIKE '%(TR)' THEN pp.pesopieza * 3
                            ELSE pp.pesopieza END * COALESCE(np.coefcompl, 1)), 1) as kg,
                   CAST(SUM(t.cantidadreparada) AS INTEGER) as cant
            FROM Trabajos t
            JOIN "FundiciónPorFecha" fpf ON fpf.códfundición = t.códfundición
            JOIN PesosDePiezas pp ON pp.códpieza = t.idpesopieza
            LEFT JOIN NombreDePiezas np ON np.id = pp.nombredepiezasid_
            WHERE fpf.fecha IS NOT NULL AND t.códfundición IS NOT NULL
              AND t.cantidadreparada > 0
              AND strftime('%Y-%m', fpf.fecha) <= strftime('%Y-%m', 'now')
            GROUP BY mes, t.idpesopieza
        """).fetchall()

        mes_set = set(mes_list)
        nombres: dict = {}

        # build rech_map[mes][cod] = kg,  rech_cnt[mes][cod] = cant
        rech_map: dict = {}
        rech_cnt: dict = {}
        for r in rech_rows:
            if r["mes"] not in mes_set:
                continue
            nombres[r["cod"]] = r["nombre"] or f"#{r['cod']}"
            rech_map.setdefault(r["mes"], {})[r["cod"]] = round(r["kg"] or 0, 1)
            rech_cnt.setdefault(r["mes"], {})[r["cod"]] = int(r["cant"] or 0)

        # build dev_map[mes][cod] = kg,  dev_cnt[mes][cod] = cant
        dev_map: dict = {}
        dev_cnt: dict = {}
        for d in dev_rows:
            try:
                m = _w2m(d["año"], d["semana"])
            except Exception:
                continue
            if m not in mes_set:
                continue
            cod = d["cod"]
            nombres.setdefault(cod, d["nombre"] or f"#{cod}")
            dev_map.setdefault(m, {}).setdefault(cod, 0)
            dev_map[m][cod] = round(dev_map[m][cod] + (d["kg"] or 0), 1)
            dev_cnt.setdefault(m, {}).setdefault(cod, 0)
            dev_cnt[m][cod] += int(d["cant"] or 0)

        # merge rech+dev kg and counts
        rd_map: dict = {}
        rd_cnt: dict = {}
        for mes in mes_list:
            rd_map[mes] = {}
            rd_cnt[mes] = {}
            for cod, kg in rech_map.get(mes, {}).items():
                rd_map[mes][cod] = round(rd_map[mes].get(cod, 0) + kg, 1)
                rd_cnt[mes][cod] = rd_cnt[mes].get(cod, 0) + rech_cnt.get(mes, {}).get(cod, 0)
            for cod, kg in dev_map.get(mes, {}).items():
                rd_map[mes][cod] = round(rd_map[mes].get(cod, 0) + kg, 1)
                rd_cnt[mes][cod] = rd_cnt[mes].get(cod, 0) + dev_cnt.get(mes, {}).get(cod, 0)

        # build rep_map[mes][cod] = kg,  rep_cnt[mes][cod] = cant
        rep_map: dict = {}
        rep_cnt: dict = {}
        nombres_rep: dict = {}
        for r in rep_rows:
            if r["mes"] not in mes_set:
                continue
            nombres_rep[r["cod"]] = r["nombre"] or f"#{r['cod']}"
            rep_map.setdefault(r["mes"], {})[r["cod"]] = round(r["kg"] or 0, 1)
            rep_cnt.setdefault(r["mes"], {})[r["cod"]] = int(r["cant"] or 0)

        # disambiguate duplicate names (same nombrepieza, different códpieza)
        def _dedup_nombres(nom_map):
            seen: dict = {}
            for cod, name in list(nom_map.items()):
                if name in seen:
                    other = seen[name]
                    nom_map[cod]   = f"{name} (#{cod})"
                    nom_map[other] = f"{name} (#{other})"
                else:
                    seen[name] = cod
        _dedup_nombres(nombres)
        _dedup_nombres(nombres_rep)

        def top5_per_month(mes_map, cnt_map, nom_map):
            result = []
            for mes in mes_list:
                items = sorted(mes_map.get(mes, {}).items(), key=lambda x: -x[1])
                top5 = items[:5]
                otros_kg = round(sum(v for _, v in items[5:]), 1)
                total = round(sum(v for _, v in items), 1)
                cnts = cnt_map.get(mes, {})
                result.append({
                    "mes": mes,
                    "top5": [{"cod": cod, "nombre": nom_map.get(cod, f"#{cod}"), "kg": round(kg, 1), "cant": cnts.get(cod, 0)} for cod, kg in top5],
                    "total": total,
                    "otros_kg": otros_kg,
                })
            return result

        return {
            "meses": mes_list,
            "rechazadas_devueltas": top5_per_month(rd_map, rd_cnt, nombres),
            "reparadas":            top5_per_month(rep_map, rep_cnt, nombres_rep),
        }
    finally:
        conn.close()


@app.get("/api/analytics/entregas_pieza")
def analytics_entregas_pieza(
    meses:   int           = Query(default=6, ge=1, le=24),
    cliente: Optional[str] = Query(None),
):
    import datetime as _dt
    conn = get_db()
    try:
        desde = (_dt.date.today().replace(day=1) - _dt.timedelta(days=1))
        for _ in range(meses - 1):
            desde = desde.replace(day=1) - _dt.timedelta(days=1)
        desde_str = desde.strftime("%Y-%m")

        extra = ""
        params: list = [desde_str]
        if cliente:
            extra = " AND np.códcliente = ?"
            params.append(cliente)

        rows = conn.execute(f"""
            SELECT substr(r.fecharemito, 1, 7)                    as mes,
                   np.id                                           as pieza_id,
                   np.nombrepieza                                  as nombre,
                   np.códigopiezapuestoporcliente                  as codigo,
                   np.códcliente                                   as cliente_cod,
                   COALESCE(c.nombrefantasía, c.nombrecliente)     as cliente_nombre,
                   SUM(id.cantidad)                                as cantidad,
                   ROUND(SUM(id.cantidad *
                       CASE WHEN np.nombrepieza LIKE '%(AD)' THEN pp.pesopieza * 2
                            WHEN np.nombrepieza LIKE '%(TR)' THEN pp.pesopieza * 3
                            ELSE pp.pesopieza END * COALESCE(np.coefcompl, 1)), 1) as kg_entregado
            FROM ItemDetalle id
            JOIN Remitos r         ON r.idnroremito  = id.idnroremito
            JOIN PesosDePiezas pp  ON pp.códpieza    = id.códpieza
            JOIN NombreDePiezas np ON np.id          = pp.nombredepiezasid_
            LEFT JOIN Clientes c   ON c.códigocliente = np.códcliente
            WHERE r.idnroremito > 10000 AND r.idnroremito < 90000
              AND id.cantidad > 0
              AND substr(r.fecharemito, 1, 7) >= ?
              AND substr(r.fecharemito, 1, 7) <= strftime('%Y-%m', 'now')
              {extra}
            GROUP BY mes, np.id
            ORDER BY mes DESC, kg_entregado DESC
        """, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/analytics/clientes/{codigo}")
def analytics_cliente_detail(codigo: str):
    conn = get_db()
    try:
        cl = conn.execute("""
            SELECT c.códigocliente as codigo,
                   c.nombrecliente as nombre,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as fantasia,
                   c.cuit,
                   c.contacto,
                   c.e_mail_para_contacto as email,
                   c.página_web as web,
                   c.condpago,
                   tcp.nombrecondiciondepago as condpago_desc,
                   c.observaciones,
                   c.observacionespararemitos as obs_remitos,
                   c.fechacreación as fecha_creacion,
                   c.porcentajefalta,
                   c.porcentajeexceso,
                   c.cantidadlímite as cantidad_limite,
                   c.ivaresponsableinscripto as iva_ri,
                   c.precioendolares as precio_usd
            FROM Clientes c
            LEFT JOIN TipoCondicionPago tcp ON c.condpago = tcp.idcondpago
            WHERE c.códigocliente = ?
        """, (codigo,)).fetchone()
        if not cl:
            raise HTTPException(404, f"Cliente '{codigo}' no encontrado")

        # Annual deliveries via ItemDetallePedido → Pedidos (with kg via pesoestablecido)
        # Use p.fechapedido as year source — fechacargaot is NULL on ~85% of rows
        ent_rows = conn.execute("""
            SELECT strftime('%Y', p.fechapedido) as año,
                   COALESCE(SUM(t.cantidadentregada), 0) as entregadas,
                   COALESCE(SUM(t.cantidadrechazada), 0) as rechazadas,
                   COALESCE(SUM(t.cantidadaprobada), 0) as aprobadas,
                   ROUND(SUM(CASE WHEN np.pesoestablecido > 0 THEN t.cantidadentregada * np.pesoestablecido ELSE 0 END), 1) as kg_entregadas,
                   ROUND(SUM(CASE WHEN np.pesoestablecido > 0 THEN t.cantidadrechazada * np.pesoestablecido ELSE 0 END), 1) as kg_rechazadas
            FROM Trabajos t
            JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido
            JOIN Pedidos p ON idp.idpedido = p.idpedido
            LEFT JOIN NombreDePiezas np ON idp.idpieza = np.id
            WHERE p.códigocliente = ? AND t.cantidadentregada > 0
              AND p.fechapedido IS NOT NULL
            GROUP BY año ORDER BY año
        """, (codigo,)).fetchall()

        # Annual returns: ItemDevolución → PesosDePiezas → NombreDePiezas (with kg)
        dev_rows = conn.execute("""
            SELECT CAST(id.año AS TEXT) as año,
                   COALESCE(SUM(id."cantidaddevolución"), 0) as devueltas,
                   ROUND(SUM(CASE WHEN np.pesoestablecido > 0 THEN id."cantidaddevolución" * np.pesoestablecido ELSE 0 END), 1) as kg_devueltas
            FROM "ItemDevolución" id
            JOIN PesosDePiezas pp ON id.códpieza = pp.códpieza
            JOIN NombreDePiezas np ON pp.nombredepiezasid_ = np.id
            WHERE np.códcliente = ?
            GROUP BY id.año ORDER BY id.año
        """, (codigo,)).fetchall()
        dev_map = {r["año"]: dict(r) for r in dev_rows}

        anual = []
        total_ent = total_dev = total_rech = 0
        for r in ent_rows:
            dv = dev_map.get(r["año"], {})
            dev = dv.get("devueltas", 0)
            ent = r["entregadas"]
            rech = r["rechazadas"]
            total_ent  += ent
            total_dev  += dev
            total_rech += rech
            anual.append({
                "año": r["año"],
                "entregadas": ent,
                "rechazadas": rech,
                "aprobadas": r["aprobadas"],
                "devueltas": dev,
                "kg_entregadas": r["kg_entregadas"] or 0.0,
                "kg_rechazadas": r["kg_rechazadas"] or 0.0,
                "kg_devueltas":  dv.get("kg_devueltas", 0.0),
                "tasa": round(dev / ent * 100, 2) if ent else 0,
            })

        # Pieces list with delivery + return totals
        piezas_rows = conn.execute("""
            SELECT np.id as pieza_id,
                   np.códigopiezapuestoporcliente as codigo_pieza,
                   np.nombrepieza as nombre,
                   np.pesoestablecido as peso,
                   COALESCE(SUM(t.cantidadentregada), 0) as entregadas,
                   COALESCE(SUM(t.cantidadrechazada), 0) as rechazadas
            FROM Trabajos t
            JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido
            JOIN Pedidos p ON idp.idpedido = p.idpedido
            JOIN NombreDePiezas np ON idp.idpieza = np.id
            WHERE p.códigocliente = ? AND t.cantidadentregada > 0
            GROUP BY np.id
            ORDER BY entregadas DESC
        """, (codigo,)).fetchall()

        # Returns per piece (batch query): ItemDevolución → PesosDePiezas → NombreDePiezas
        dev_pieza: dict[int, int] = {}
        if piezas_rows:
            ids = tuple(r["pieza_id"] for r in piezas_rows)
            ph = ",".join("?" * len(ids))
            dp = conn.execute(
                f'SELECT pp.nombredepiezasid_, COALESCE(SUM(id."cantidaddevolución"), 0) as devueltas '
                f'FROM "ItemDevolución" id '
                f'JOIN PesosDePiezas pp ON id.códpieza = pp.códpieza '
                f'WHERE pp.nombredepiezasid_ IN ({ph}) GROUP BY pp.nombredepiezasid_',
                ids,
            ).fetchall()
            dev_pieza = {r["nombredepiezasid_"]: r["devueltas"] for r in dp}

        # probeta config por pieza
        probeta_set: set[int] = set()
        if piezas_rows:
            ids = tuple(r["pieza_id"] for r in piezas_rows)
            ph = ",".join("?" * len(ids))
            pb = conn.execute(
                f"SELECT pieza_id FROM _piezas_config WHERE pieza_id IN ({ph}) AND pide_probeta_traccion = 1",
                ids,
            ).fetchall()
            probeta_set = {r["pieza_id"] for r in pb}

        piezas = []
        for r in piezas_rows:
            ent = r["entregadas"]
            dev = dev_pieza.get(r["pieza_id"], 0)
            piezas.append({
                "pieza_id": r["pieza_id"],
                "codigo_pieza": r["codigo_pieza"],
                "nombre": r["nombre"],
                "peso": r["peso"] if r["peso"] and r["peso"] > 0 else None,
                "entregadas": ent,
                "rechazadas": r["rechazadas"],
                "devueltas": dev,
                "tasa_devolucion": round(dev / ent * 100, 2) if ent else 0,
                "pide_probeta_traccion": r["pieza_id"] in probeta_set,
            })

        todas_con_probeta = bool(piezas) and len(probeta_set) == len(piezas)

        mensual_cl = conn.execute("""
            SELECT strftime('%Y', p.fechapedido) AS año,
                   CAST(strftime('%m', p.fechapedido) AS INTEGER) AS mes,
                   COALESCE(SUM(t.cantidadentregada), 0) AS entregadas,
                   COALESCE(SUM(t.cantidadrechazada), 0) AS rechazadas,
                   ROUND(SUM(CASE WHEN np.pesoestablecido > 0 THEN t.cantidadentregada * np.pesoestablecido ELSE 0 END), 1) AS kg_entregadas,
                   ROUND(SUM(CASE WHEN np.pesoestablecido > 0 THEN t.cantidadrechazada * np.pesoestablecido ELSE 0 END), 1) AS kg_rechazadas
            FROM Trabajos t
            JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido
            JOIN Pedidos p ON idp.idpedido = p.idpedido
            LEFT JOIN NombreDePiezas np ON idp.idpieza = np.id
            WHERE p.códigocliente = ? AND t.cantidadentregada > 0
              AND p.fechapedido IS NOT NULL
            GROUP BY año, mes ORDER BY año, mes
        """, (codigo,)).fetchall()

        return {
            "cliente": dict(cl),
            "total_entregadas": total_ent,
            "total_devueltas": total_dev,
            "total_rechazadas": total_rech,
            "tasa_total": round(total_dev / total_ent * 100, 2) if total_ent else 0,
            "n_piezas": len(piezas),
            "anual": anual,
            "mensual": [dict(r) for r in mensual_cl],
            "piezas": piezas,
            "todas_con_probeta": todas_con_probeta,
            "raw": _raw(conn, "Clientes", "códigocliente", codigo),
        }
    finally:
        conn.close()


@app.get("/api/analytics/piezas/{pieza_id}")
def analytics_pieza_detail(pieza_id: int):
    conn = get_db()
    try:
        pieza = conn.execute("""
            SELECT id as pieza_id, códcliente as codigo_cliente,
                   códigopiezapuestoporcliente as codigo_pieza,
                   nombrepieza as nombre,
                   pesoestablecido as peso
            FROM NombreDePiezas WHERE id = ?
        """, (pieza_id,)).fetchone()
        if not pieza:
            raise HTTPException(404, f"Pieza {pieza_id} no encontrada")

        # Annual delivery from Trabajos via ItemDetallePedido → Pedidos
        # Use p.fechapedido — fechacargaot is NULL on ~85% of rows
        ent_rows = conn.execute("""
            SELECT strftime('%Y', p.fechapedido) as año,
                   COALESCE(SUM(t.cantidadentregada), 0) as entregadas,
                   COALESCE(SUM(t.cantidadrechazada), 0) as rechazadas,
                   COALESCE(SUM(t.cantidadaprobada), 0) as aprobadas
            FROM Trabajos t
            JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido
            JOIN Pedidos p ON idp.idpedido = p.idpedido
            WHERE idp.idpieza = ? AND t.cantidadentregada > 0
              AND p.fechapedido IS NOT NULL
            GROUP BY año ORDER BY año
        """, (pieza_id,)).fetchall()

        # Annual returns: ItemDevolución → PesosDePiezas (filter by NombreDePiezas.id)
        dev_rows = conn.execute("""
            SELECT CAST(id.año AS TEXT) as año,
                   COALESCE(SUM(id."cantidaddevolución"), 0) as devueltas
            FROM "ItemDevolución" id
            JOIN PesosDePiezas pp ON id.códpieza = pp.códpieza
            WHERE pp.nombredepiezasid_ = ?
            GROUP BY id.año ORDER BY id.año
        """, (pieza_id,)).fetchall()
        dev_map = {r["año"]: r["devueltas"] for r in dev_rows}
        ent_map = {r["año"]: r for r in ent_rows}

        # Merge years from both sources so return-only years also appear
        all_years = sorted(set(ent_map) | set(dev_map))
        anual = []
        for año in all_years:
            er = ent_map.get(año)
            dev = dev_map.get(año, 0)
            ent = er["entregadas"] if er else 0
            anual.append({
                "año": año,
                "entregadas": ent,
                "rechazadas": er["rechazadas"] if er else 0,
                "aprobadas": er["aprobadas"] if er else 0,
                "devueltas": dev,
                "tasa": round(dev / ent * 100, 2) if ent else 0,
            })

        defectos = _sum_defects(conn, pieza_id)

        # InfTécnica* tables — each has (idpieza, resumen, detalle, [extras])
        def _inf(table, extras=None):
            row = conn.execute(
                f'SELECT * FROM "{table}" WHERE idpieza = ?', (pieza_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d.pop("idpieza", None)
            return d

        inftecnica = {
            "pieza":    _inf("InfTécnicaPieza"),
            "calidad":  _inf("InfTécnicaCalidad"),
            "rebaba":   _inf("InfTécnicaRebaba"),
            "noyeria":  _inf("InfTécnicaNoyería"),
            "moldeo":   _inf("InfTécnicaMoldeo"),
            "colada":   _inf("InfTécnicaColada"),
        }

        cfg = conn.execute(
            "SELECT pide_probeta_traccion FROM _piezas_config WHERE pieza_id = ?", (pieza_id,)
        ).fetchone()
        pide_probeta = bool(cfg["pide_probeta_traccion"]) if cfg else False

        peso_pz = pieza["peso"] or 0
        mensual_pz = conn.execute("""
            SELECT strftime('%Y', p.fechapedido) AS año,
                   CAST(strftime('%m', p.fechapedido) AS INTEGER) AS mes,
                   COALESCE(SUM(t.cantidadentregada), 0) AS entregadas,
                   COALESCE(SUM(t.cantidadrechazada), 0) AS rechazadas
            FROM Trabajos t
            JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido
            JOIN Pedidos p ON idp.idpedido = p.idpedido
            WHERE idp.idpieza = ? AND t.cantidadentregada > 0
              AND p.fechapedido IS NOT NULL
            GROUP BY año, mes ORDER BY año, mes
        """, (pieza_id,)).fetchall()
        # kg computed from known peso (single pieza, no JOIN needed)
        mensual_pz_out = []
        for r in mensual_pz:
            d2 = dict(r)
            d2["kg_entregadas"] = round(d2["entregadas"] * peso_pz, 1) if peso_pz else 0.0
            d2["kg_rechazadas"] = round(d2["rechazadas"] * peso_pz, 1) if peso_pz else 0.0
            mensual_pz_out.append(d2)

        return {
            "pieza": dict(pieza),
            "anual": anual,
            "mensual": mensual_pz_out,
            "defectos": defectos,
            "inftecnica": inftecnica,
            "pide_probeta_traccion": pide_probeta,
            "raw": _raw(conn, "NombreDePiezas", "id", pieza_id),
        }
    finally:
        conn.close()


@app.get("/api/analytics/defectos")
def analytics_defectos(
    cliente: Optional[str] = Query(None),
    año: Optional[int] = Query(None),
):
    conn = get_db()
    try:
        where_parts: list[str] = []
        params: list = []

        if cliente:
            where_parts.append("np.códcliente = ?")
            params.append(cliente)
        if año:
            where_parts.append("id.año = ?")
            params.append(año)

        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        cols = _defect_cols(conn)
        if cols:
            expr = ", ".join(f'COALESCE(SUM(id."{c}"), 0) as "{c}"' for c in cols)
            row = conn.execute(
                f'SELECT {expr} FROM "ItemDevolución" id '
                f'JOIN PesosDePiezas pp ON id.códpieza = pp.códpieza '
                f'JOIN NombreDePiezas np ON pp.nombredepiezasid_ = np.id {where}',
                params,
            ).fetchone()
            defectos = [
                {"tipo": c, "label": _DEFECT_NAMES[c], "total": row[c]}
                for c in cols if row[c] > 0
            ]
            defectos.sort(key=lambda x: x["total"], reverse=True)
        else:
            defectos = []

        top_piezas = conn.execute(
            f'SELECT np.id as pieza_id, np.nombrepieza as nombre, '
            f'COALESCE(SUM(id."cantidaddevolución"), 0) as devueltas '
            f'FROM "ItemDevolución" id '
            f'JOIN PesosDePiezas pp ON id.códpieza = pp.códpieza '
            f'JOIN NombreDePiezas np ON pp.nombredepiezasid_ = np.id '
            f'{where} GROUP BY np.id ORDER BY devueltas DESC LIMIT 15',
            params,
        ).fetchall()

        return {
            "defectos": defectos,
            "top_piezas": [dict(r) for r in top_piezas],
        }
    finally:
        conn.close()


# ── Shared query helper ───────────────────────────────────────────────────────

@app.get("/api/estados_ot")
def get_estados_ot():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT códigoestado AS codigo, leyendaestado AS leyenda, "
            "comentarios, prioridad FROM Estados ORDER BY prioridad"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


_EST_LABELS: dict = {
    'K': 'Cumplido Auto',   'k': 'Cumplido Auto',
    'D': 'Cumplido Manual', 'd': 'Cumplido Manual',
    'A': 'Anulado c/Imp',   'a': 'Anulado c/Imp',
    'B': 'Anulado s/Imp',
    'R': 'Remito s/Ctrl',
    'L': 'Listo p/Entregar',
    'P': 'Programado',
    'I': 'Inicial',
    'F': 'Fundido',
    'O': 'Impreso',
    'C': 'Confirmado',
    'Y': 'Para Cumplir',
    'T': 'Trabajando',
    'M': 'Moldeado',
}

def _est_map(conn: sqlite3.Connection) -> dict:
    return _EST_LABELS


# ── Pedidos ───────────────────────────────────────────────────────────────────

@app.get("/api/pedidos")
def get_pedidos(
    año: Optional[int] = Query(None),
    meses: Optional[int] = Query(None, ge=1, le=24),
    cliente: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    search:    List[str]      = Query(default=[]),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    conn = get_db()
    try:
        est = _est_map(conn)
        wp, pa = [], []
        if año:
            wp.append("strftime('%Y', p.fechapedido) = ?"); pa.append(str(año))
        if meses:
            wp.append("date(p.fechapedido) >= date('now', ? || ' months')"); pa.append(f"-{meses}")
        if cliente:
            wp.append("p.códigocliente = ?"); pa.append(cliente)
        if estado:
            wp.append("p.estadopedido = ?"); pa.append(estado)
        for term in search:
            s = f"%{term.strip()}%"
            wp.append("(COALESCE(c.nombrefantasía, c.nombrecliente) LIKE ? OR CAST(p.nropedido AS TEXT) LIKE ? OR p.observaciones LIKE ? OR p.códigocliente LIKE ?)")
            pa.extend([s, s, s, s])
        where = ("WHERE " + " AND ".join(wp)) if wp else ""
        base = "FROM Pedidos p JOIN Clientes c ON p.códigocliente = c.códigocliente"

        total = conn.execute(f"SELECT COUNT(*) {base} {where}", pa).fetchone()[0]
        offset = (page - 1) * page_size
        rows = conn.execute(f"""
            SELECT p.idpedido, p.nropedido, p.códigocliente,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as cliente_nombre,
                   p.fechapedido, p.estadopedido, p.observaciones
            {base} {where} ORDER BY p.fechapedido DESC LIMIT ? OFFSET ?
        """, pa + [page_size, offset]).fetchall()

        ids = tuple(r["idpedido"] for r in rows)
        cnt_map: dict = {}
        if ids:
            ph = ",".join("?" * len(ids))
            for r in conn.execute(
                f"SELECT idpedido, COUNT(*) as n FROM ItemDetallePedido WHERE idpedido IN ({ph}) GROUP BY idpedido", ids
            ).fetchall():
                cnt_map[r["idpedido"]] = r["n"]

        result = []
        for r in rows:
            d = dict(r)
            d["estado_label"] = est.get(d["estadopedido"] or "", d["estadopedido"] or "")
            d["n_items"] = cnt_map.get(d["idpedido"], 0)
            result.append(d)

        return {"rows": result, "total": total, "page": page, "page_size": page_size,
                "pages": max(1, (total + page_size - 1) // page_size)}
    finally:
        conn.close()


@app.get("/api/pedidos/{pedido_id}")
def get_pedido_detail(pedido_id: int):
    conn = get_db()
    try:
        est = _est_map(conn)
        p = conn.execute("""
            SELECT p.idpedido, p.nropedido, p.códigocliente,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as cliente_nombre,
                   p.fechapedido, p.estadopedido, p.observaciones,
                   p.parastock, p.pagoanticipado, p.confirmarpedido
            FROM Pedidos p JOIN Clientes c ON p.códigocliente = c.códigocliente
            WHERE p.idpedido = ?
        """, (pedido_id,)).fetchone()
        if not p:
            raise HTTPException(404, f"Pedido {pedido_id} no encontrado")

        items = conn.execute("""
            SELECT idp.iditempedido, idp.cantidadpedida, idp.fechadeentrega,
                   idp.estadoitem, idp.fechasolicitadaentrega,
                   np.id as pieza_id, np.nombrepieza,
                   np.códigopiezapuestoporcliente as codigo_pieza,
                   COALESCE(SUM(t.cantidadentregada), 0) as entregadas,
                   COALESCE(SUM(t.cantidadrechazada), 0) as rechazadas,
                   COALESCE(SUM(t.cantidadproducida), 0) as producidas,
                   last_ot.ot_id,
                   t_last.estadotrabajo AS ot_estado
            FROM ItemDetallePedido idp
            JOIN NombreDePiezas np ON idp.idpieza = np.id
            LEFT JOIN Trabajos t ON idp.iditempedido = t.iditempedido
            LEFT JOIN (
                SELECT iditempedido, MAX(iditemtrabajo) AS ot_id
                FROM Trabajos GROUP BY iditempedido
            ) last_ot ON last_ot.iditempedido = idp.iditempedido
            LEFT JOIN Trabajos t_last ON t_last.iditemtrabajo = last_ot.ot_id
            WHERE idp.idpedido = ?
            GROUP BY idp.iditempedido
            ORDER BY np.nombrepieza
        """, (pedido_id,)).fetchall()

        pd_dict = dict(p)
        pd_dict["estado_label"] = est.get(pd_dict["estadopedido"] or "", pd_dict["estadopedido"] or "")
        result_items = []
        for r in items:
            d = dict(r)
            d["estado_label"] = est.get(d["estadoitem"] or "", d["estadoitem"] or "")
            code = d.get("ot_estado") or ""
            d["ot_estado"] = est.get(code, code) if code else None
            result_items.append(d)
        return {"pedido": pd_dict, "items": result_items,
                "raw": _raw(conn, "Pedidos", "idpedido", pedido_id)}
    finally:
        conn.close()


# ── Remitos ───────────────────────────────────────────────────────────────────

@app.get("/api/remitos")
def get_remitos(
    año: Optional[int] = Query(None),
    meses: Optional[int] = Query(None, ge=1, le=24),
    cliente: Optional[str] = Query(None),
    search:    List[str]  = Query(default=[]),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    conn = get_db()
    try:
        wp, pa = [], []
        if año:
            wp.append("strftime('%Y', r.fecharemito) = ?"); pa.append(str(año))
        if meses:
            wp.append("date(r.fecharemito) >= date('now', ? || ' months')"); pa.append(f"-{meses}")
        if cliente:
            wp.append("r.códigocliente = ?"); pa.append(cliente)
        for term in search:
            s = f"%{term.strip()}%"
            wp.append("(COALESCE(c.nombrefantasía, c.nombrecliente) LIKE ? OR CAST(r.idnroremito AS TEXT) LIKE ? OR r.observaciones LIKE ? OR r.códigocliente LIKE ?)")
            pa.extend([s, s, s, s])
        where = ("WHERE " + " AND ".join(wp)) if wp else ""
        base = "FROM Remitos r JOIN Clientes c ON r.códigocliente = c.códigocliente"

        total = conn.execute(f"SELECT COUNT(*) {base} {where}", pa).fetchone()[0]
        offset = (page - 1) * page_size
        rows = conn.execute(f"""
            SELECT r.idnroremito, r.códigocliente,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as cliente_nombre,
                   r.fecharemito, r.marcacontrolado, r.observaciones
            {base} {where} ORDER BY r.fecharemito DESC LIMIT ? OFFSET ?
        """, pa + [page_size, offset]).fetchall()

        ids = tuple(r["idnroremito"] for r in rows)
        cnt_map2: dict = {}
        if ids:
            ph = ",".join("?" * len(ids))
            for r in conn.execute(
                f"SELECT idnroremito, COUNT(*) as n, COALESCE(SUM(cantidad),0) as total_pzas FROM ItemDetalle WHERE idnroremito IN ({ph}) GROUP BY idnroremito", ids
            ).fetchall():
                cnt_map2[r["idnroremito"]] = (r["n"], r["total_pzas"])

        result = []
        for r in rows:
            d = dict(r)
            ic = cnt_map2.get(d["idnroremito"], (0, 0))
            d["n_items"] = ic[0]; d["total_piezas"] = ic[1]
            result.append(d)
        return {"rows": result, "total": total, "page": page, "page_size": page_size,
                "pages": max(1, (total + page_size - 1) // page_size)}
    finally:
        conn.close()


@app.get("/api/remitos/{remito_id}")
def get_remito_detail(remito_id: int):
    conn = get_db()
    try:
        r = conn.execute("""
            SELECT r.idnroremito, r.códigocliente,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as cliente_nombre,
                   r.fecharemito, r.marcacontrolado, r.observaciones
            FROM Remitos r JOIN Clientes c ON r.códigocliente = c.códigocliente
            WHERE r.idnroremito = ?
        """, (remito_id,)).fetchone()
        if not r:
            raise HTTPException(404, f"Remito {remito_id} no encontrado")

        items = conn.execute("""
            SELECT d.iditemdetalle, d.cantidad, d.iditemtrabajo,
                   np.id as pieza_id, np.nombrepieza,
                   np.códigopiezapuestoporcliente as codigo_pieza
            FROM ItemDetalle d
            JOIN PesosDePiezas pp ON d.códpieza = pp.códpieza
            JOIN NombreDePiezas np ON pp.nombredepiezasid_ = np.id
            WHERE d.idnroremito = ?
            GROUP BY d.iditemdetalle
            ORDER BY np.nombrepieza
        """, (remito_id,)).fetchall()

        return {"remito": dict(r), "items": [dict(i) for i in items],
                "raw": _raw(conn, "Remitos", "idnroremito", remito_id)}
    finally:
        conn.close()


# ── Piezas ────────────────────────────────────────────────────────────────────

@app.get("/api/piezas")
def get_piezas(
    cliente:   Optional[str]  = Query(None),
    search:    List[str]      = Query(default=[]),
    habilitado: Optional[bool] = Query(None),
    page:      int            = Query(1, ge=1),
    page_size: int            = Query(50, ge=1, le=200),
):
    conn = get_db()
    try:
        wp, pa = [], []
        if cliente:
            wp.append("np.códcliente = ?"); pa.append(cliente)
        for term in search:
            s = f"%{term.strip()}%"
            wp.append(
                "(np.nombrepieza LIKE ? OR np.códigopiezapuestoporcliente LIKE ?"
                " OR c.códigocliente LIKE ? OR c.nombrecliente LIKE ? OR c.nombrefantasía LIKE ?)"
            )
            pa.extend([s, s, s, s, s])
        if habilitado is not None:
            wp.append("np.habilitado = ?"); pa.append(1 if habilitado else 0)
        where = ("WHERE " + " AND ".join(wp)) if wp else ""
        base = "FROM NombreDePiezas np LEFT JOIN Clientes c ON np.códcliente = c.códigocliente"

        total = conn.execute(f"SELECT COUNT(*) {base} {where}", pa).fetchone()[0]
        offset = (page - 1) * page_size
        rows = conn.execute(f"""
            SELECT np.id, np.códcliente,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as cliente_nombre,
                   np.códigopiezapuestoporcliente as codigo_pieza,
                   np.nombrepieza, np.tipofacturación, np.habilitado,
                   np.pesoestablecido, np.especificacionmaterial
            {base} {where} ORDER BY np.nombrepieza LIMIT ? OFFSET ?
        """, pa + [page_size, offset]).fetchall()

        return {"rows": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size,
                "pages": max(1, (total + page_size - 1) // page_size)}
    finally:
        conn.close()


# ── Modelos ───────────────────────────────────────────────────────────────────

@app.get("/api/modelos")
def get_modelos(
    cliente:    Optional[str]  = Query(None),
    search:     List[str]      = Query(default=[]),
    habilitado: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    conn = get_db()
    try:
        wp, pa = [], []
        if cliente:
            wp.append("m.códigodueño = ?"); pa.append(cliente)
        for term in search:
            s = f"%{term.strip()}%"
            wp.append("(m.nombremodelo LIKE ? OR m.descripciónmodelo LIKE ? OR CAST(m.códigomodelo AS TEXT) LIKE ? OR m.códigomodelopuestoporcliente LIKE ? OR c.códigocliente LIKE ? OR c.nombrecliente LIKE ? OR c.nombrefantasía LIKE ?)")
            pa.extend([s, s, s, s, s, s, s])
        if habilitado is not None:
            wp.append("m.habilitado = ?"); pa.append(1 if habilitado else 0)
        where = ("WHERE " + " AND ".join(wp)) if wp else ""
        base = """FROM Modelos m
            LEFT JOIN Clientes c ON m.códigodueño = c.códigocliente
            LEFT JOIN TiposModelo tm ON m.tipomodelo = tm.tipomodelo"""

        total = conn.execute(f"SELECT COUNT(*) {base} {where}", pa).fetchone()[0]
        offset = (page - 1) * page_size
        rows = conn.execute(f"""
            SELECT m.códigomodelo, m.códigodueño,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as cliente_nombre,
                   m.códigomodelopuestoporcliente as codigo_cliente,
                   m.nombremodelo, m.descripciónmodelo,
                   tm.nombretipomodelo as tipo_label,
                   m.existencia, m.habilitado, m.fechaultimomovimiento
            {base} {where}
            ORDER BY m.existencia DESC, m.nombremodelo LIMIT ? OFFSET ?
        """, pa + [page_size, offset]).fetchall()

        return {"rows": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size,
                "pages": max(1, (total + page_size - 1) // page_size)}
    finally:
        conn.close()


@app.get("/api/modelos/{modelo_id}")
def get_modelo_detail(modelo_id: int):
    conn = get_db()
    try:
        m = conn.execute("""
            SELECT m.códigomodelo, m.códigodueño,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as cliente_nombre,
                   m.códigomodelopuestoporcliente as codigo_cliente,
                   m.nombremodelo, m.descripciónmodelo,
                   tm.nombretipomodelo as tipo_label,
                   m.existencia, m.habilitado, m.cantidadmodelos,
                   m.noyeríapasta, m.noyeríashell, m.noyeríafenólica,
                   m.fechaultimomovimiento, m.ultimomovimientomodelo,
                   m.documentoultimomovimiento
            FROM Modelos m
            LEFT JOIN Clientes c ON m.códigodueño = c.códigocliente
            LEFT JOIN TiposModelo tm ON m.tipomodelo = tm.tipomodelo
            WHERE m.códigomodelo = ?
        """, (modelo_id,)).fetchone()
        if not m:
            raise HTTPException(404, f"Modelo {modelo_id} no encontrado")

        piezas = conn.execute("""
            SELECT np.id as pieza_id, np.nombrepieza,
                   np.códigopiezapuestoporcliente as codigo_pieza, np.habilitado
            FROM PiezasPorModelo pm
            JOIN NombreDePiezas np ON pm.códpieza = np.id
            WHERE pm.códmodelo = ?
            ORDER BY np.nombrepieza
        """, (modelo_id,)).fetchall()

        movimientos = conn.execute("""
            SELECT m.fechaoperación, UPPER(m.operación) as operación,
                   COALESCE(t.descripcion, UPPER(m.operación)) as operacion_desc,
                   m.observaciones
            FROM MovimientoModelos m
            LEFT JOIN TiposOperacionModelo t ON UPPER(m.operación) = t.codigo
            WHERE m.códigomodelo = ?
            ORDER BY m.fechaoperación DESC LIMIT 30
        """, (modelo_id,)).fetchall()

        fotos = conn.execute(
            """SELECT idfoto, enlacefotomodelo, enlacefotooriginal, comentario
               FROM FotosDeModelos
               WHERE códigomodelo = ? AND (habilitada = 'True' OR habilitada = 1)
               ORDER BY idfoto""",
            (modelo_id,)
        ).fetchall()

        return {
            "modelo": dict(m),
            "piezas": [dict(r) for r in piezas],
            "movimientos": [dict(r) for r in movimientos],
            "fotos": [dict(r) for r in fotos],
            "fotos_count": len(fotos),
            "fotos_disponibles": bool(FOTOS_MODELOS_PATHS),
            "raw": _raw(conn, "Modelos", "códigomodelo", modelo_id),
        }
    finally:
        conn.close()


@app.get("/api/modelos/{modelo_id}/nav")
def get_modelo_nav(
    modelo_id: int,
    cliente: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    habilitado: Optional[bool] = Query(None),
):
    conn = get_db()
    try:
        wp, pa = [], []
        if cliente:
            wp.append("m.códigodueño = ?"); pa.append(cliente)
        if search:
            s = f"%{search.strip()}%"
            wp.append("(m.nombremodelo LIKE ? OR m.descripciónmodelo LIKE ? OR CAST(m.códigomodelo AS TEXT) LIKE ? OR m.códigomodelopuestoporcliente LIKE ?)")
            pa.extend([s, s, s, s])
        if habilitado is not None:
            wp.append("m.habilitado = ?"); pa.append(1 if habilitado else 0)
        where = ("WHERE " + " AND ".join(wp)) if wp else ""
        row = conn.execute(f"""
            WITH ordered AS (
                SELECT m.códigomodelo,
                       LAG(m.códigomodelo)  OVER (ORDER BY m.existencia DESC, m.nombremodelo) AS prev_id,
                       LEAD(m.códigomodelo) OVER (ORDER BY m.existencia DESC, m.nombremodelo) AS next_id,
                       ROW_NUMBER()         OVER (ORDER BY m.existencia DESC, m.nombremodelo) AS position,
                       COUNT(*)             OVER ()                                           AS total
                FROM Modelos m
                LEFT JOIN Clientes c ON m.códigodueño = c.códigocliente
                {where}
            )
            SELECT prev_id, next_id, position, total FROM ordered WHERE códigomodelo = ?
        """, pa + [modelo_id]).fetchone()
        if not row:
            return {"prev_id": None, "next_id": None, "position": None, "total": 0}
        return dict(row)
    finally:
        conn.close()


@app.get("/api/piezas/{pieza_id}/modelos")
def get_modelos_for_pieza(pieza_id: int):
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT m.códigomodelo, m.nombremodelo,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as cliente_nombre
            FROM PiezasPorModelo pm
            JOIN Modelos m ON pm.códmodelo = m.códigomodelo
            LEFT JOIN Clientes c ON m.códigodueño = c.códigocliente
            WHERE pm.códpieza = ?
            ORDER BY m.nombremodelo
        """, (pieza_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/fotos/modelos/{filename}")
def get_foto_modelo(filename: str):
    if not FOTOS_MODELOS_PATHS or not _fotos_disponibles():
        raise HTTPException(503, "Fotos de modelos no disponibles")
    try:
        foto_path = _buscar_foto_modelo(filename)
    except Exception:
        raise HTTPException(503, "Fotos de modelos no disponibles")
    if foto_path is None:
        raise HTTPException(404, f"Foto '{filename}' no encontrada")
    return FileResponse(str(foto_path))


# ── Fundiciones ───────────────────────────────────────────────────────────────

@app.get("/api/fundiciones")
def get_fundiciones(
    año:     Optional[int]  = Query(None),
    cerrada: Optional[bool] = Query(None),
    search:  List[str]      = Query(default=[]),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    conn = get_db()
    try:
        wp, pa = [], []
        if año:
            wp.append("strftime('%Y', fpf.fecha) = ?"); pa.append(str(año))
        if cerrada is not None:
            wp.append("f.planillacerrada = ?"); pa.append("True" if cerrada else "False")
        for term in search:
            wp.append("CAST(f.códfundición AS TEXT) LIKE ?"); pa.append(f"%{term.strip()}%")
        where = ("WHERE " + " AND ".join(wp)) if wp else ""
        base = """FROM Fundiciones f
            LEFT JOIN (SELECT códfundición, MIN(fecha) as fecha FROM FundiciónPorFecha GROUP BY códfundición) fpf
              ON f.códfundición = fpf.códfundición"""

        total = conn.execute(f"SELECT COUNT(*) {base} {where}", pa).fetchone()[0]
        offset = (page - 1) * page_size
        rows = conn.execute(f"""
            SELECT f.códfundición, fpf.fecha, f.horastallerefectivos,
                   f.planillacerrada, f.kgdevol
            {base} {where} ORDER BY fpf.fecha DESC LIMIT ? OFFSET ?
        """, pa + [page_size, offset]).fetchall()

        fids = tuple(r["códfundición"] for r in rows)
        item_cnt: dict = {}
        if fids:
            ph = ",".join("?" * len(fids))
            for r in conn.execute(
                f'SELECT códfundición, COUNT(*) as n, COALESCE(SUM(cantidadmoldeada),0) as total '
                f'FROM "ItemProducción" WHERE códfundición IN ({ph}) GROUP BY códfundición', fids
            ).fetchall():
                item_cnt[r["códfundición"]] = (r["n"], r["total"])

        result = []
        for r in rows:
            d = dict(r)
            ic = item_cnt.get(d["códfundición"], (0, 0))
            d["n_items"] = ic[0]; d["total_moldeadas"] = ic[1]
            result.append(d)

        return {"rows": result, "total": total, "page": page, "page_size": page_size,
                "pages": max(1, (total + page_size - 1) // page_size)}
    finally:
        conn.close()


@app.get("/api/fundiciones/mensual")
def get_fundiciones_mensual(meses: int = Query(default=12, ge=3, le=36)):
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT strftime('%Y-%m', fpf.fecha)        as mes,
                   COUNT(DISTINCT f.códfundición)      as n_coladas,
                   SUM(f.horastallerefectivos)         as hs_efectivas,
                   SUM(f.horastalleragencia)           as hs_agencia,
                   SUM(f.horasnoproductivas)           as hs_no_productivas,
                   SUM(f.kgarenascrap)                 as kg_scrap,
                   SUM(f.kgdevol)                      as kg_devol
            FROM Fundiciones f
            JOIN "FundiciónPorFecha" fpf ON fpf.códfundición = f.códfundición
            WHERE fpf.fecha IS NOT NULL
              AND strftime('%Y-%m', fpf.fecha) <= strftime('%Y-%m', 'now')
            GROUP BY mes
            ORDER BY mes DESC
            LIMIT ?
        """, (meses,)).fetchall()
        return {"meses": [dict(r) for r in reversed(list(rows))]}
    finally:
        conn.close()


@app.get("/api/fundiciones/{fund_id}")
def get_fundicion_detail(fund_id: int):
    conn = get_db()
    try:
        f = conn.execute("""
            SELECT f.códfundición, fpf.fecha, f.horastallerefectivos,
                   f.horastalleragencia, f.horasnoproductivas,
                   f.planillacerrada, f.planillaimpresa,
                   f.kgdevol, f.kgarenascrap, f.nrocrisol, f.nrocampaña
            FROM Fundiciones f
            LEFT JOIN (SELECT códfundición, MIN(fecha) as fecha FROM FundiciónPorFecha GROUP BY códfundición) fpf
              ON f.códfundición = fpf.códfundición
            WHERE f.códfundición = ?
        """, (fund_id,)).fetchone()
        if not f:
            raise HTTPException(404, f"Fundición {fund_id} no encontrada")

        items = conn.execute("""
            SELECT ip.iditemproducción, ip.cantidadmoldeada, ip.cantidadaprobada,
                   np.id as pieza_id, np.nombrepieza,
                   np.códigopiezapuestoporcliente as codigo_pieza
            FROM "ItemProducción" ip
            JOIN PesosDePiezas pp ON ip.códpieza = pp.códpieza
            JOIN NombreDePiezas np ON pp.nombredepiezasid_ = np.id
            WHERE ip.códfundición = ?
            GROUP BY ip.iditemproducción
            ORDER BY np.nombrepieza
        """, (fund_id,)).fetchall()

        mat = conn.execute("""
            SELECT m.norma as material_nombre,
                   im.morfologíagrafito, im.matriz,
                   im.carbono, im.silicio, im.manganeso, im.fósforo, im.azufre,
                   im.valorresistencia, im.valoralargamiento, im.fechaaprobación
            FROM InformesMateriales im
            LEFT JOIN Materiales m ON im.códmaterial = m.especificaciónmaterial
            WHERE im.códfundición = ?
        """, (fund_id,)).fetchone()

        return {
            "fundicion": dict(f),
            "items": [dict(r) for r in items],
            "material": dict(mat) if mat else None,
            "raw": _raw(conn, "Fundiciones", "códfundición", fund_id),
        }
    finally:
        conn.close()


# ── Documentos ────────────────────────────────────────────────────────────────

@app.get("/api/documentos")
def get_documentos(
    año:     Optional[int] = Query(None),
    cliente: Optional[str] = Query(None),
    tipo:    Optional[int] = Query(None),
    search:  List[str]     = Query(default=[]),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    conn = get_db()
    try:
        wp, pa = [], []
        if año:
            wp.append("strftime('%Y', d.fechaemisión) = ?"); pa.append(str(año))
        if cliente:
            wp.append("d.códigocliente = ?"); pa.append(cliente)
        if tipo:
            wp.append("d.idtipodoc = ?"); pa.append(tipo)
        for term in search:
            s = f"%{term.strip()}%"
            wp.append("(d.descripción LIKE ? OR d.nrodoc LIKE ? OR COALESCE(c.nombrefantasía, c.nombrecliente) LIKE ? OR d.códigocliente LIKE ?)")
            pa.extend([s, s, s, s])
        where = ("WHERE " + " AND ".join(wp)) if wp else ""
        base = """FROM Documentos d
            LEFT JOIN Clientes c ON d.códigocliente = c.códigocliente
            LEFT JOIN TiposDocumento td ON d.idtipodoc = td.idtipodoc"""

        total = conn.execute(f"SELECT COUNT(*) {base} {where}", pa).fetchone()[0]
        offset = (page - 1) * page_size
        rows = conn.execute(f"""
            SELECT d.iddoc, d.nrodoc, d.fechaemisión,
                   td.inictipodoc as tipo_abrev, td.nomtipodoc as tipo_label,
                   d.códigocliente,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as cliente_nombre,
                   d.descripción, d.fechacierre, d.ok
            {base} {where} ORDER BY d.fechaemisión DESC LIMIT ? OFFSET ?
        """, pa + [page_size, offset]).fetchall()

        return {"rows": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size,
                "pages": max(1, (total + page_size - 1) // page_size)}
    finally:
        conn.close()


# ── Personal ───────────────────────────────────────────────────────────────────

_EXCLUIR_NOMBRES = {"-", "NN", "DbAdministrator", "AgenciaTaller",
                    "AgenciaAdmin", "IRAM", "Batiplane"}


def _sector_names(conn) -> dict:
    rows = conn.execute("SELECT id, nombre FROM _sectores").fetchall()
    return {r[0]: r[1] for r in rows}


# ── Endpoints: Sectores ────────────────────────────────────────────────────────

@app.get("/api/personal/sectores")
def get_sectores(user: dict = Depends(_get_auth_user)):
    conn = get_db()
    try:
        rows = conn.execute("SELECT id, nombre FROM _sectores ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.post("/api/personal/sectores")
async def post_sector(req: Request, admin: dict = Depends(_require_admin)):
    body = await req.json()
    sid  = body.get("id")
    nombre = str(body.get("nombre") or "").strip()
    if sid is None or not nombre:
        raise HTTPException(400, "id y nombre requeridos")
    conn = get_db()
    try:
        conn.execute("INSERT INTO _sectores (id, nombre) VALUES (?,?)", (int(sid), nombre))
        conn.commit()
        return {"id": int(sid), "nombre": nombre}
    finally:
        conn.close()


@app.put("/api/personal/sectores/{sector_id}")
async def put_sector(sector_id: int, req: Request, admin: dict = Depends(_require_admin)):
    body = await req.json()
    nombre = str(body.get("nombre") or "").strip()
    if not nombre:
        raise HTTPException(400, "nombre requerido")
    conn = get_db()
    try:
        conn.execute("UPDATE _sectores SET nombre=? WHERE id=?", (nombre, sector_id))
        conn.commit()
        return {"id": sector_id, "nombre": nombre}
    finally:
        conn.close()


@app.delete("/api/personal/sectores/{sector_id}")
def delete_sector(sector_id: int, admin: dict = Depends(_require_admin)):
    conn = get_db()
    try:
        conn.execute("DELETE FROM _sectores WHERE id=?", (sector_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/personal")
def get_personal(
    hasta:        Optional[str] = Query(default=None),
    meses:        int           = Query(default=12, ge=1, le=60),
    desvinculados: bool         = Query(default=False),
):
    conn = get_db()
    try:
        fecha_hasta = date_cls.fromisoformat(hasta) if hasta else date_cls.today()
        # N months back: subtract meses from (year*12 + month)
        total_m = fecha_hasta.year * 12 + fecha_hasta.month - meses
        fecha_desde = date_cls((total_m - 1) // 12, (total_m - 1) % 12 + 1, 1)
        f_desde = fecha_desde.isoformat()
        f_hasta = fecha_hasta.isoformat()

        wdev = "" if desvinculados else "AND COALESCE(pe.sector, r.codsector) <> 97"
        rows = conn.execute(f"""
            SELECT r.codigoresponsable             AS id,
                   r.apellidoynombre               AS nombre,
                   r.nombre                        AS nombre_propio,
                   r.apellido,
                   COALESCE(pe.sector, r.codsector) AS sector,
                   r.cargo,
                   r.comentarios,
                   COALESCE(pe.legajo, '')          AS legajo,
                   COALESCE(pe.apellido, '')         AS ext_apellido,
                   COALESCE(pe.nombre, '')           AS ext_nombre,
                   COALESCE(SUM(
                       CASE WHEN substr(h.fecha,1,10) BETWEEN ? AND ?
                                 AND h.horastrabajadas > 0
                            THEN h.horastrabajadas END
                   ), 0) AS hs_periodo,
                   COALESCE(SUM(
                       CASE WHEN h.horastrabajadas > 0
                            THEN h.horastrabajadas END
                   ), 0) AS hs_total,
                   COUNT(DISTINCT
                       CASE WHEN substr(h.fecha,1,10) BETWEEN ? AND ?
                                 AND h.horastrabajadas > 0
                            THEN substr(h.fecha,1,7) END
                   ) AS n_periodos,
                   MAX(
                       CASE WHEN h.horastrabajadas > 0
                            THEN substr(h.fecha,1,10) END
                   ) AS ultimo_registro,
                   COALESCE(aus.aus_enf, 0) AS aus_enf,
                   COALESCE(aus.aus_fca, 0) AS aus_fca,
                   COALESCE(aus.aus_fsa, 0) AS aus_fsa,
                   COALESCE(aus.aus_acc, 0) AS aus_acc
            FROM _v_responsables r
            LEFT JOIN _personal_ext pe ON pe.access_id = r.codigoresponsable
            LEFT JOIN HorasPorFecha h
                   ON h."códigoresponsable" = r.codigoresponsable
            LEFT JOIN (
                SELECT access_id,
                       SUM(enfermedad)      AS aus_enf,
                       SUM(falta_con_aviso) AS aus_fca,
                       SUM(falta_sin_aviso) AS aus_fsa,
                       SUM(accidente)       AS aus_acc
                FROM _personal_ausentismo
                WHERE fecha LIKE strftime('%Y','now') || '-%'
                GROUP BY access_id
            ) aus ON aus.access_id = r.codigoresponsable
            WHERE (r.codigoresponsable BETWEEN 1 AND 2000 OR pe.access_id IS NOT NULL)
              AND COALESCE(pe.sector, r.codsector) NOT IN (0, 99)
              AND r.apellidoynombre NOT IN
                  ('-','NN','DbAdministrator','AgenciaTaller',
                   'AgenciaAdmin','IRAM','Batiplane')
              {wdev}
            GROUP BY r.codigoresponsable
            ORDER BY hs_periodo DESC, r.apellidoynombre
        """, (f_desde, f_hasta, f_desde, f_hasta)).fetchall()
        snames = _sector_names(conn)
        aus_anio = fecha_hasta.year
        return [
            {**dict(r),
             "sector_nombre": snames.get(r["sector"], str(r["sector"])),
             "aus_anio": aus_anio}
            for r in rows
        ]
    finally:
        conn.close()


@app.get("/api/personal/{persona_id}")
def get_personal_detail(persona_id: int, periodos: int = Query(default=36)):
    conn = get_db()
    try:
        p = conn.execute("""
            SELECT r.codigoresponsable AS id,
                   r.apellidoynombre  AS nombre,
                   r.nombre           AS nombre_propio,
                   r.apellido,
                   COALESCE(pe.sector, r.codsector) AS sector,
                   r.cargo,
                   r.comentarios,
                   COALESCE(pe.legajo, '')   AS legajo,
                   COALESCE(pe.apellido, '') AS ext_apellido,
                   COALESCE(pe.nombre, '')   AS ext_nombre,
                   pe.sector                 AS sector_override
            FROM _v_responsables r
            LEFT JOIN _personal_ext pe ON pe.access_id = r.codigoresponsable
            WHERE r.codigoresponsable = ?
        """, (persona_id,)).fetchone()
        if not p:
            raise HTTPException(404, f"Persona {persona_id} no encontrada")

        hist = conn.execute("""
            SELECT substr(fecha, 1, 7) AS mes,
                   MAX(substr(fecha, 1, 10)) AS fecha_registro,
                   SUM(horastrabajadas) AS hs
            FROM HorasPorFecha
            WHERE "códigoresponsable" = ? AND horastrabajadas > 0
            GROUP BY mes ORDER BY mes DESC LIMIT ?
        """, (persona_id, periodos)).fetchall()

        historial = list(reversed([dict(h) for h in hist]))
        total_hs = sum(h["hs"] for h in historial)
        avg_hs = round(total_hs / len(historial), 1) if historial else 0

        snames = _sector_names(conn)
        persona = dict(p)
        persona["sector_nombre"] = snames.get(p["sector"], str(p["sector"]))

        return {
            "persona":         persona,
            "historial":       historial,
            "total_hs":        total_hs,
            "avg_hs_periodo":  avg_hs,
            "n_periodos":      len(historial),
        }
    finally:
        conn.close()


@app.put("/api/personal/{persona_id}/ext")
async def put_personal_ext(persona_id: int, req: Request, admin: dict = Depends(_require_admin)):
    body = await req.json()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO _personal_ext (access_id, legajo, apellido, nombre) VALUES (?,?,?,?) "
            "ON CONFLICT(access_id) DO UPDATE SET legajo=excluded.legajo, apellido=excluded.apellido, nombre=excluded.nombre",
            (persona_id, str(body.get("legajo") or ""), str(body.get("apellido") or ""), str(body.get("nombre") or ""))
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.put("/api/personal/{persona_id}/sector")
async def put_personal_sector(persona_id: int, req: Request, admin: dict = Depends(_require_admin)):
    body = await req.json()
    sector_id = body.get("sector_id")
    conn = get_db()
    try:
        if sector_id is None:
            # Quitar override: volver al sector del Access
            conn.execute(
                "UPDATE _personal_ext SET sector=NULL WHERE access_id=?",
                (persona_id,)
            )
        else:
            # Verificar que el sector existe
            exists = conn.execute("SELECT 1 FROM _sectores WHERE id=?", (int(sector_id),)).fetchone()
            if not exists:
                raise HTTPException(404, f"Sector {sector_id} no existe")
            # Upsert _personal_ext para asegurarse que la fila existe
            conn.execute(
                "INSERT INTO _personal_ext (access_id, sector) VALUES (?,?) "
                "ON CONFLICT(access_id) DO UPDATE SET sector=excluded.sector",
                (persona_id, int(sector_id))
            )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


def _ausen_calc(r: dict) -> dict:
    """Agrega totales y porcentajes calculados a un registro de ausentismo."""
    dl  = r.get("dias_laborables") or 0
    enf = r.get("enfermedad")      or 0
    fca = r.get("falta_con_aviso") or 0
    fsa = r.get("falta_sin_aviso") or 0
    acc = r.get("accidente")       or 0
    vac = r.get("vacaciones")      or 0
    total_real  = enf + fca + fsa + acc
    total_pond  = enf * 0.5 + fca * 0.5 + fsa * 1.0
    pct_real    = round(total_real / dl * 100, 2) if dl else 0
    pct_pond    = round(total_pond / dl * 100, 2) if dl else 0
    return {**r,
            "total_faltas_real":      round(total_real, 2),
            "total_faltas_ponderado": round(total_pond, 2),
            "pct_faltas_real":        pct_real,
            "pct_faltas_ponderado":   pct_pond}


@app.get("/api/personal/{persona_id}/ausentismo")
def get_ausentismo(persona_id: int, user: dict = Depends(_get_auth_user)):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM _personal_ausentismo WHERE access_id=? ORDER BY fecha",
            (persona_id,)
        ).fetchall()
        return [_ausen_calc(dict(r)) for r in rows]
    finally:
        conn.close()


@app.post("/api/personal/{persona_id}/ausentismo")
async def post_ausentismo(persona_id: int, req: Request, user: dict = Depends(_get_auth_user)):
    body = await req.json()
    fecha = str(body.get("fecha") or "").strip()
    if not fecha:
        raise HTTPException(400, "fecha requerida (YYYY-MM)")
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO _personal_ausentismo "
            "(access_id,fecha,dias_laborables,enfermedad,falta_con_aviso,falta_sin_aviso,"
            "accidente,vacaciones,llegada_tarde,retiro_anticipado) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (persona_id, fecha,
             int(body.get("dias_laborables") or 0),
             float(body.get("enfermedad") or 0),
             float(body.get("falta_con_aviso") or 0),
             float(body.get("falta_sin_aviso") or 0),
             float(body.get("accidente") or 0),
             float(body.get("vacaciones") or 0),
             int(body.get("llegada_tarde") or 0),
             int(body.get("retiro_anticipado") or 0))
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM _personal_ausentismo WHERE access_id=? AND fecha=?",
            (persona_id, fecha)
        ).fetchone()
        return _ausen_calc(dict(row))
    finally:
        conn.close()


@app.put("/api/personal/{persona_id}/ausentismo/{fecha}")
async def put_ausentismo(persona_id: int, fecha: str, req: Request, user: dict = Depends(_get_auth_user)):
    body = await req.json()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id FROM _personal_ausentismo WHERE access_id=? AND fecha=?",
            (persona_id, fecha)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Registro no encontrado")
        conn.execute(
            "UPDATE _personal_ausentismo SET "
            "dias_laborables=?,enfermedad=?,falta_con_aviso=?,falta_sin_aviso=?,"
            "accidente=?,vacaciones=?,llegada_tarde=?,retiro_anticipado=? "
            "WHERE access_id=? AND fecha=?",
            (int(body.get("dias_laborables") or 0),
             float(body.get("enfermedad") or 0),
             float(body.get("falta_con_aviso") or 0),
             float(body.get("falta_sin_aviso") or 0),
             float(body.get("accidente") or 0),
             float(body.get("vacaciones") or 0),
             int(body.get("llegada_tarde") or 0),
             int(body.get("retiro_anticipado") or 0),
             persona_id, fecha)
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM _personal_ausentismo WHERE access_id=? AND fecha=?",
            (persona_id, fecha)
        ).fetchone()
        return _ausen_calc(dict(updated))
    finally:
        conn.close()


@app.delete("/api/personal/{persona_id}/ausentismo/{fecha}")
def delete_ausentismo(persona_id: int, fecha: str, user: dict = Depends(_get_auth_user)):
    conn = get_db()
    try:
        conn.execute(
            "DELETE FROM _personal_ausentismo WHERE access_id=? AND fecha=?",
            (persona_id, fecha)
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/personal/{persona_id}/produccion")
def get_personal_produccion(
    persona_id: int,
    anio_desde: int = Query(default=2020),
):
    conn = get_db()
    try:
        totales = conn.execute("""
            SELECT COALESCE(SUM(t.cantidadproducida), 0) AS producidas,
                   COALESCE(SUM(t.cantidadaprobada),  0) AS aprobadas,
                   COALESCE(SUM(t.cantidadrechazada), 0) AS rechazadas,
                   COALESCE(SUM(t.cantidadreparada),  0) AS reparadas,
                   COALESCE(SUM(t.cantidadentregada - t.cantidaddestock), 0) AS entregadas,
                   COALESCE(SUM(t.cantidadenviadastock), 0) AS a_stock
            FROM Trabajos t
            JOIN "FundiciónPorFecha" fpf ON fpf."códfundición" = t."códfundición"
            WHERE t.códigoresponsable1 = ? AND fpf.fecha >= ? AND t."códfundición" IS NOT NULL
        """, (persona_id, f"{anio_desde}-01-01")).fetchone()

        meses = conn.execute("""
            SELECT substr(fpf.fecha, 1, 7)             AS mes,
                   COALESCE(SUM(t.cantidadproducida),0) AS producidas,
                   COALESCE(SUM(t.cantidadaprobada), 0) AS aprobadas,
                   COALESCE(SUM(t.cantidadrechazada),0) AS rechazadas,
                   COALESCE(SUM(t.cantidadreparada), 0) AS reparadas,
                   COALESCE(SUM(t.cantidadentregada - t.cantidaddestock),0) AS entregadas,
                   COALESCE(SUM(t.cantidadenviadastock),0) AS a_stock
            FROM Trabajos t
            JOIN "FundiciónPorFecha" fpf ON fpf."códfundición" = t."códfundición"
            WHERE t.códigoresponsable1 = ? AND fpf.fecha >= ? AND t."códfundición" IS NOT NULL
            GROUP BY mes ORDER BY mes
        """, (persona_id, f"{anio_desde}-01-01")).fetchall()

        piezas = conn.execute("""
            SELECT np.nombrepieza                       AS pieza,
                   COALESCE(SUM(t.cantidadproducida),0) AS producidas,
                   COALESCE(SUM(t.cantidadaprobada), 0) AS aprobadas,
                   COALESCE(SUM(t.cantidadrechazada),0) AS rechazadas
            FROM Trabajos t
            JOIN "FundiciónPorFecha" fpf ON fpf."códfundición" = t."códfundición"
            LEFT JOIN PesosDePiezas pp   ON pp."códpieza" = t.idpesopieza
            LEFT JOIN NombreDePiezas np  ON np.id = pp."nombredepiezasid_"
            WHERE t.códigoresponsable1 = ? AND fpf.fecha >= ? AND t."códfundición" IS NOT NULL
            GROUP BY np.id ORDER BY producidas DESC LIMIT 15
        """, (persona_id, f"{anio_desde}-01-01")).fetchall()

        t = dict(totales)
        prod = t["producidas"] or 0
        apro = t["aprobadas"] or 0
        t["pct_aprobacion"] = round(apro / prod * 100, 1) if prod else 0
        return {
            "totales": t,
            "meses":   [dict(r) for r in meses],
            "piezas":  [dict(r) for r in piezas],
            "tiene_produccion": prod > 0,
        }
    finally:
        conn.close()


@app.get("/api/personal/{persona_id}/reparaciones")
def get_personal_reparaciones(
    persona_id: int,
    anio_desde: int = Query(default=2020),
):
    conn = get_db()
    try:
        totales = conn.execute("""
            SELECT COALESCE(SUM(t.cantidadreparada), 0) AS total_reparadas,
                   COUNT(*)                             AS n_ots
            FROM "SoluciónReparacionInterna" sri
            JOIN Trabajos t ON t.iditemtrabajo = sri.iditemtrabajo
            JOIN "FundiciónPorFecha" fpf ON fpf."códfundición" = t."códfundición"
            WHERE t.códigoresponsable1 = ? AND fpf.fecha >= ?
        """, (persona_id, f"{anio_desde}-01-01")).fetchone()

        meses = conn.execute("""
            SELECT substr(fpf.fecha, 1, 7)             AS mes,
                   COALESCE(SUM(t.cantidadreparada), 0) AS reparadas
            FROM "SoluciónReparacionInterna" sri
            JOIN Trabajos t ON t.iditemtrabajo = sri.iditemtrabajo
            JOIN "FundiciónPorFecha" fpf ON fpf."códfundición" = t."códfundición"
            WHERE t.códigoresponsable1 = ? AND fpf.fecha >= ?
            GROUP BY mes ORDER BY mes
        """, (persona_id, f"{anio_desde}-01-01")).fetchall()

        motivos = conn.execute("""
            SELECT sri.descripciónrepar                AS motivo,
                   COUNT(*)                            AS n_ots,
                   COALESCE(SUM(t.cantidadreparada),0) AS total
            FROM "SoluciónReparacionInterna" sri
            JOIN Trabajos t ON t.iditemtrabajo = sri.iditemtrabajo
            JOIN "FundiciónPorFecha" fpf ON fpf."códfundición" = t."códfundición"
            WHERE t.códigoresponsable1 = ? AND fpf.fecha >= ?
            GROUP BY sri.descripciónrepar ORDER BY total DESC LIMIT 15
        """, (persona_id, f"{anio_desde}-01-01")).fetchall()

        piezas = conn.execute("""
            SELECT np.nombrepieza                       AS pieza,
                   COALESCE(SUM(t.cantidadreparada), 0) AS reparadas
            FROM "SoluciónReparacionInterna" sri
            JOIN Trabajos t ON t.iditemtrabajo = sri.iditemtrabajo
            JOIN "FundiciónPorFecha" fpf ON fpf."códfundición" = t."códfundición"
            LEFT JOIN PesosDePiezas pp  ON pp."códpieza" = t.idpesopieza
            LEFT JOIN NombreDePiezas np ON np.id = pp."nombredepiezasid_"
            WHERE t.códigoresponsable1 = ? AND fpf.fecha >= ?
            GROUP BY np.id ORDER BY reparadas DESC LIMIT 15
        """, (persona_id, f"{anio_desde}-01-01")).fetchall()

        t = dict(totales)
        return {
            "total_reparadas": t["total_reparadas"] or 0,
            "n_ots":           t["n_ots"] or 0,
            "meses":           [dict(r) for r in meses],
            "motivos":         [dict(r) for r in motivos],
            "piezas":          [dict(r) for r in piezas],
        }
    finally:
        conn.close()


@app.get("/api/personal/{persona_id}/rechazos")
def get_personal_rechazos(
    persona_id: int,
    anio_desde: int = Query(default=2020),
):
    conn = get_db()
    try:
        # Totales globales del período
        totales = conn.execute("""
            SELECT COALESCE(SUM(t.cantidadproducida), 0) AS producidas,
                   COALESCE(SUM(t.cantidadrechazada), 0) AS rechazadas,
                   COALESCE(SUM(t.cantidadreparada),  0) AS reparadas,
                   COALESCE(SUM(t.cantidadaprobada),  0) AS aprobadas
            FROM Trabajos t
            JOIN "FundiciónPorFecha" fpf ON fpf."códfundición" = t."códfundición"
            WHERE t.códigoresponsable1 = ?
              AND fpf.fecha >= ?
              AND t."códfundición" IS NOT NULL
        """, (persona_id, f"{anio_desde}-01-01")).fetchone()

        # Tendencia mensual
        meses = conn.execute("""
            SELECT substr(fpf.fecha, 1, 7)             AS mes,
                   COALESCE(SUM(t.cantidadproducida),0) AS producidas,
                   COALESCE(SUM(t.cantidadrechazada),0) AS rechazadas,
                   COALESCE(SUM(t.cantidadreparada), 0) AS reparadas
            FROM Trabajos t
            JOIN "FundiciónPorFecha" fpf ON fpf."códfundición" = t."códfundición"
            WHERE t.códigoresponsable1 = ?
              AND fpf.fecha >= ?
              AND t."códfundición" IS NOT NULL
            GROUP BY mes ORDER BY mes
        """, (persona_id, f"{anio_desde}-01-01")).fetchall()

        # Top piezas rechazadas
        piezas = conn.execute("""
            SELECT np.nombrepieza                       AS pieza,
                   COALESCE(SUM(t.cantidadrechazada),0) AS rechazadas,
                   COALESCE(SUM(t.cantidadproducida),0) AS producidas
            FROM Trabajos t
            JOIN "FundiciónPorFecha" fpf ON fpf."códfundición" = t."códfundición"
            LEFT JOIN PesosDePiezas pp   ON pp."códpieza" = t.idpesopieza
            LEFT JOIN NombreDePiezas np  ON np.id = pp."nombredepiezasid_"
            WHERE t.códigoresponsable1 = ?
              AND fpf.fecha >= ?
              AND t."códfundición" IS NOT NULL
              AND t.cantidadrechazada > 0
            GROUP BY np.id ORDER BY rechazadas DESC LIMIT 10
        """, (persona_id, f"{anio_desde}-01-01")).fetchall()

        t = dict(totales)
        prod = t["producidas"] or 0
        rech = t["rechazadas"] or 0
        t["pct_rechazo"] = round(rech / prod * 100, 2) if prod else 0

        meses_data = []
        for r in meses:
            p2 = r["producidas"] or 0
            r2 = r["rechazadas"] or 0
            meses_data.append({
                "mes":        r["mes"],
                "producidas": p2,
                "rechazadas": r2,
                "reparadas":  r["reparadas"] or 0,
                "pct_rechazo": round(r2 / p2 * 100, 2) if p2 else 0,
            })

        return {
            "totales": t,
            "meses":   meses_data,
            "piezas":  [dict(r) for r in piezas],
            "es_moldeador": rech > 0,
        }
    finally:
        conn.close()


@app.get("/api/personal/{persona_id}/ots")
def get_personal_ots(persona_id: int):
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT t.iditemtrabajo                      AS id,
                   t.estadotrabajo                      AS estado,
                   np.nombrepieza                       AS pieza,
                   c.nombrecliente                      AS cliente,
                   t.cantidad                           AS cantidad,
                   COALESCE(t.cantidadproducida, 0)     AS producidas,
                   COALESCE(t.cantidadaprobada,  0)     AS aprobadas,
                   COALESCE(t.cantidadrechazada, 0)     AS rechazadas,
                   COALESCE(t.cantidadentregada - t.cantidaddestock, 0) AS entregadas,
                   COALESCE(t.cantidadpendiente, 0)     AS pendiente,
                   substr(t.fechacargaot,   1, 10)      AS fecha_carga,
                   substr(t.fechaprevista,  1, 10)      AS fecha_prevista,
                   t.obsot                              AS obs,
                   (SELECT fdm.enlacefotomodelo
                    FROM PiezasPorModelo ppm2
                    JOIN FotosDeModelos fdm
                      ON fdm."códigomodelo" = ppm2."códmodelo"
                     AND fdm.habilitada = 'True'
                    WHERE ppm2."códpieza" = np.id
                    LIMIT 1)                            AS foto
            FROM Trabajos t
            JOIN ItemDetallePedido idp ON idp.iditempedido = t.iditempedido
            JOIN NombreDePiezas np     ON np.id = idp.idpieza
            LEFT JOIN Clientes c       ON c."códigocliente" = np."códcliente"
            WHERE t."códigoresponsable1" = ?
            ORDER BY t.fechacargaot DESC
        """, (persona_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/personal/{persona_id}/rechazos_mes")
def get_personal_rechazos_mes(
    persona_id: int,
    mes: str = Query(...),          # YYYY-MM
):
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT substr(fpf.fecha, 1, 10)        AS fecha_colada,
                   np.nombrepieza                  AS pieza,
                   t."códmaterial"                 AS material,
                   t.cantidadproducida             AS producidas,
                   t.cantidadaprobada              AS aprobadas,
                   t.cantidadrechazada             AS rechazadas,
                   t.cantidadreparada              AS reparadas,
                   t.cantidadentregada - t.cantidaddestock AS entregadas,
                   t.cantidadenviadastock                 AS a_stock,
                   sri.descripción                 AS motivo
            FROM Trabajos t
            JOIN "FundiciónPorFecha" fpf ON fpf."códfundición" = t."códfundición"
            LEFT JOIN PesosDePiezas pp   ON pp."códpieza" = t.idpesopieza
            LEFT JOIN NombreDePiezas np  ON np.id = pp."nombredepiezasid_"
            LEFT JOIN "SoluciónRechazoInterno" sri ON sri.iditemtrabajo = t.iditemtrabajo
            WHERE t."códigoresponsable1" = ?
              AND substr(fpf.fecha, 1, 7) = ?
              AND t."códfundición" IS NOT NULL
            ORDER BY fpf.fecha, t.iditemtrabajo
        """, (persona_id, mes)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/documentos/stats")
def get_documentos_stats(anio_desde: int = Query(default=2018)):
    conn = get_db()
    try:
        totales = conn.execute("""
            SELECT td.inictipodoc as tipo, td.nomtipodoc as nombre,
                   COUNT(*) as total,
                   SUM(CASE WHEN d.fechacierre IS NULL THEN 1 ELSE 0 END) as abiertos
            FROM Documentos d
            JOIN TiposDocumento td ON td.idtipodoc = d.idtipodoc
            GROUP BY d.idtipodoc ORDER BY total DESC
        """).fetchall()

        por_anio = conn.execute("""
            SELECT strftime('%Y', d.fechaemisión) as anio,
                   td.inictipodoc as tipo,
                   COUNT(*) as n
            FROM Documentos d
            JOIN TiposDocumento td ON td.idtipodoc = d.idtipodoc
            WHERE strftime('%Y', d.fechaemisión) >= ?
            GROUP BY anio, d.idtipodoc ORDER BY anio, td.inictipodoc
        """, (str(anio_desde),)).fetchall()

        return {
            "totales": [dict(r) for r in totales],
            "por_anio": [dict(r) for r in por_anio],
        }
    finally:
        conn.close()


@app.get("/api/documentos/abiertos")
def get_documentos_abiertos(tipo: Optional[str] = Query(None)):
    conn = get_db()
    try:
        extra = ""
        params: list = []
        if tipo:
            extra = "AND td.inictipodoc = ?"
            params.append(tipo.upper())
        rows = conn.execute(f"""
            SELECT d.iddoc, d.nrodoc, substr(d.fechaemisión, 1, 10) as fecha,
                   td.inictipodoc as tipo,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as cliente,
                   d.códigocliente,
                   d.descripción,
                   CAST(julianday('now') - julianday(d.fechaemisión) AS INTEGER) as dias_abierto
            FROM Documentos d
            JOIN TiposDocumento td ON td.idtipodoc = d.idtipodoc
            LEFT JOIN Clientes c ON c.códigocliente = d.códigocliente
            WHERE d.fechacierre IS NULL {extra}
            ORDER BY d.fechaemisión
        """, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/documentos/por_cliente")
def get_documentos_por_cliente(
    tipo: str = Query(default="IRC"),
    anio_desde: int = Query(default=2020),
):
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT strftime('%Y', d.fechaemisión) as anio,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as cliente,
                   d.códigocliente,
                   COUNT(*) as n_docs,
                   SUM(CASE WHEN d.fechacierre IS NULL THEN 1 ELSE 0 END) as abiertos
            FROM Documentos d
            JOIN TiposDocumento td ON td.idtipodoc = d.idtipodoc
            LEFT JOIN Clientes c ON c.códigocliente = d.códigocliente
            WHERE td.inictipodoc = ? AND strftime('%Y', d.fechaemisión) >= ?
            GROUP BY anio, d.códigocliente
            ORDER BY anio DESC, n_docs DESC
        """, (tipo.upper(), str(anio_desde))).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/api/documentos/{doc_id}")
def get_documento_detail(doc_id: int):
    conn = get_db()
    try:
        d = conn.execute("""
            SELECT d.iddoc, d.nrodoc, d.fechaemisión, d.creación,
                   td.inictipodoc as tipo_abrev, td.nomtipodoc as tipo_label,
                   d.códigocliente,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as cliente_nombre,
                   d.descripción, d.causas, d.acctomadas, d.observaciones,
                   d.fechacierre, d.fprevcierre, d.ok,
                   d.fechaverif, d.fprevverif, d.accioneseficaces,
                   d.devolución, d.mejora, d.refirc, d.piezas
            FROM Documentos d
            LEFT JOIN Clientes c ON d.códigocliente = c.códigocliente
            LEFT JOIN TiposDocumento td ON d.idtipodoc = td.idtipodoc
            WHERE d.iddoc = ?
        """, (doc_id,)).fetchone()
        if not d:
            raise HTTPException(404, f"Documento {doc_id} no encontrado")

        dd = dict(d)
        items_reclamo = []
        if dd.get("refirc"):
            items_reclamo = conn.execute("""
                SELECT ir.iditemreclamo, ir.cantidadreclamo, ir.cerrado, ir.verificado,
                       np.id as pieza_id, np.nombrepieza,
                       np.códigopiezapuestoporcliente as codigo_pieza
                FROM ItemReclamo ir
                LEFT JOIN NombreDePiezas np ON ir.refpieza = np.id
                WHERE ir.refdocreclamo = ?
                ORDER BY np.nombrepieza
            """, (dd["refirc"],)).fetchall()

        return {"documento": dd, "items_reclamo": [dict(r) for r in items_reclamo]}
    finally:
        conn.close()


# ── Trabajos ─────────────────────────────────────────────────────────────────

@app.get("/api/trabajos/meta")
def get_trabajos_meta():
    """Años y estados disponibles para los filtros de la pestaña Trabajos."""
    conn = get_db()
    try:
        años = [
            r["año"] for r in conn.execute("""
                SELECT DISTINCT strftime('%Y', p.fechapedido) as año
                FROM Trabajos t
                JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido
                JOIN Pedidos p ON idp.idpedido = p.idpedido
                WHERE p.fechapedido IS NOT NULL ORDER BY año DESC
            """).fetchall()
            if r["año"]
        ]

        est_desc = _est_map(conn)
        estados_usados = conn.execute("""
            SELECT estadotrabajo as codigo, COUNT(*) as n
            FROM Trabajos WHERE estadotrabajo IS NOT NULL
            GROUP BY estadotrabajo ORDER BY n DESC
        """).fetchall()
        estados = [
            {"codigo": r["codigo"], "label": est_desc.get(r["codigo"], r["codigo"]), "count": r["n"]}
            for r in estados_usados
        ]

        return {"años": años, "estados": estados}
    finally:
        conn.close()


@app.get("/api/trabajos")
def get_trabajos(
    año: Optional[int] = Query(None),
    meses: Optional[int] = Query(None, ge=1, le=24),
    cliente: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    search:  List[str]     = Query(default=[]),
    ot_id: Optional[int] = Query(None),
    origen: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    conn = get_db()
    try:
        est_desc = _est_map(conn)

        where_parts: list[str] = []
        params: list = []

        if ot_id:
            where_parts.append("t.iditemtrabajo = ?")
            params.append(ot_id)
        if origen:
            where_parts.append("t.origen = ?")
            params.append(origen)
        if año:
            where_parts.append("strftime('%Y', p.fechapedido) = ?")
            params.append(str(año))
        if meses:
            where_parts.append("date(p.fechapedido) >= date('now', ? || ' months')")
            params.append(f"-{meses}")
        if cliente:
            where_parts.append("p.códigocliente = ?")
            params.append(cliente)
        if estado:
            where_parts.append("t.estadotrabajo = ?")
            params.append(estado)
        for term in search:
            s = f"%{term.strip()}%"
            where_parts.append(
                "(COALESCE(c.nombrefantasía, c.nombrecliente) LIKE ?"
                " OR np.nombrepieza LIKE ?"
                " OR CAST(t.obsot AS TEXT) LIKE ?"
                " OR p.códigocliente LIKE ?"
                " OR np.códigopiezapuestoporcliente LIKE ?)"
            )
            params.extend([s, s, s, s, s])

        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        base_from = """
            FROM Trabajos t
            JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido
            JOIN Pedidos p ON idp.idpedido = p.idpedido
            JOIN Clientes c ON p.códigocliente = c.códigocliente
            JOIN NombreDePiezas np ON idp.idpieza = np.id
        """

        summary = conn.execute(
            f"""SELECT COUNT(*) as total_ots,
                COALESCE(SUM(t.cantidadproducida), 0) as producidas,
                COALESCE(SUM(t.cantidadrechazada), 0) as rechazadas,
                COALESCE(SUM(t.cantidadaprobada), 0) as aprobadas,
                COALESCE(SUM(t.cantidadentregada), 0) as entregadas
            {base_from} {where}""",
            params,
        ).fetchone()

        total = summary["total_ots"]
        offset = (page - 1) * page_size

        rows = conn.execute(
            f"""SELECT t.iditemtrabajo, t.origen,
                t.fechaprevista, p.fechapedido as fecha,
                t.códfundición as fundicion, t.estadotrabajo,
                p.códigocliente as codigo_cliente,
                COALESCE(c.nombrefantasía, c.nombrecliente) as cliente_nombre,
                np.nombrepieza, np.códigopiezapuestoporcliente as codigo_pieza,
                t.cantidadproducir, t.cantidadproducida, t.cantidadrechazada,
                t.cantidadaprobada, t.cantidadentregada, t.obsot
            {base_from} {where}
            ORDER BY t.iditemtrabajo DESC
            LIMIT ? OFFSET ?""",
            params + [page_size, offset],
        ).fetchall()

        result_rows = []
        for r in rows:
            d = dict(r)
            d["estado_label"] = est_desc.get(d["estadotrabajo"] or "", d["estadotrabajo"] or "")
            result_rows.append(d)

        return {
            "rows": result_rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "summary": {
                "producidas": summary["producidas"],
                "rechazadas": summary["rechazadas"],
                "aprobadas": summary["aprobadas"],
                "entregadas": summary["entregadas"],
            },
        }
    finally:
        conn.close()


@app.get("/api/trabajos/pipeline")
def get_trabajos_pipeline():
    conn = get_db()
    try:
        est_desc = _est_map(conn)
        rows = conn.execute("""
            SELECT t.estadotrabajo                     as estado,
                   COUNT(*)                            as n_ots,
                   COALESCE(SUM(t.cantidad), 0)        as cant_pedida,
                   COALESCE(SUM(t.cantidadproducida), 0) as producidas,
                   COALESCE(SUM(t.cantidadaprobada),  0) as aprobadas
            FROM Trabajos t
            WHERE t.estadotrabajo IS NOT NULL
              AND upper(t.estadotrabajo) NOT IN ('K','D','A','B')
            GROUP BY t.estadotrabajo
            ORDER BY n_ots DESC
        """).fetchall()
        total = sum(r["n_ots"] for r in rows)
        estados = []
        for r in rows:
            cod = r["estado"]
            estados.append({
                "estado":      cod,
                "label":       est_desc.get(cod, cod),
                "n_ots":       r["n_ots"],
                "cant_pedida": r["cant_pedida"],
                "producidas":  r["producidas"],
                "aprobadas":   r["aprobadas"],
            })
        return {"total": total, "estados": estados}
    finally:
        conn.close()


@app.get("/api/trabajos/{trabajo_id}")
def get_trabajo_detail(trabajo_id: int):
    conn = get_db()
    try:
        est_desc = _est_map(conn)
        row = conn.execute("""
            SELECT
                t.iditemtrabajo, t.iditempedido, t.origen,
                t.códfundición, t.cuño, t.cantidad, t.fechaprevista,
                t.cantidadproducir, t.códigoresponsable1, t.códigoresponsable2,
                t.cantidadproducida, t.cantidadrechazada, t.cantidadreparada,
                t.cantidadaprobada, t.cantidadentregada,
                t.estadotrabajo, t.códmaterial, t.códdeagregados,
                t.fechacargaot, t.fundidor, t.moldeado,
                t.piezasentacho, t.piezassueltas, t.obsot,
                t.cantidadfundida, t.iditemproducción,
                p.nropedido, p.códigocliente, p.fechapedido,
                COALESCE(c.nombrefantasía, c.nombrecliente) as cliente_nombre,
                idp.cantidadpedida, idp.fechadeentrega,
                np.id as pieza_id, np.nombrepieza,
                np.códcliente as cod_pieza_cliente,
                np.códigopiezapuestoporcliente as codigo_pieza,
                np."acuñar_" as acunar, np."emitirinforme_" as emitirinforme,
                np."últimocuño" as ultimo_cuno,
                r1.apellidoynombreresponsable as responsable_nombre,
                mat.norma as material_nombre
            FROM Trabajos t
            JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido
            JOIN Pedidos p ON idp.idpedido = p.idpedido
            JOIN Clientes c ON p.códigocliente = c.códigocliente
            JOIN NombreDePiezas np ON idp.idpieza = np.id
            LEFT JOIN Responsables r1 ON t.códigoresponsable1 = r1.códigoresponsable
            LEFT JOIN Materiales mat ON t.códmaterial = mat.especificaciónmaterial
            WHERE t.iditemtrabajo = ?
        """, (trabajo_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Trabajo {trabajo_id} no encontrado")
        d = dict(row)
        d["estado_label"] = est_desc.get(d["estadotrabajo"] or "", d["estadotrabajo"] or "")

        noyeria = conn.execute("""
            SELECT m.códigomodelo, m.nombremodelo, m.existencia,
                   m.noyeríapasta   AS noy_pasta,
                   m.noyeríashell   AS noy_shell,
                   m.noyeríafenólica AS noy_fenolica,
                   (SELECT fdm.enlacefotomodelo
                    FROM FotosDeModelos fdm
                    WHERE fdm."códigomodelo" = m."códigomodelo"
                      AND fdm.habilitada = 'True'
                    LIMIT 1) AS foto
            FROM PiezasPorModelo pm
            JOIN Modelos m ON pm.códmodelo = m.códigomodelo
            WHERE pm.códpieza = ?
            ORDER BY m.existencia DESC, m.códigomodelo DESC
            LIMIT 1
        """, (d["pieza_id"],)).fetchone()

        prod = None
        if d.get("iditemproducción"):
            prod = conn.execute("""
                SELECT cantidadmoldeada, cantidadaprobada, tipomoldeo
                FROM ItemProducción WHERE iditemproducción = ?
            """, (d["iditemproducción"],)).fetchone()

        peso_molde = None
        if noyeria:
            pm_row = conn.execute(
                "SELECT pesodemolde FROM PesosDeMoldes WHERE id = ?",
                (noyeria["códigomodelo"],)
            ).fetchone()
            if pm_row:
                peso_molde = pm_row["pesodemolde"]

        return {
            "trabajo": d,
            "noyeria": dict(noyeria) if noyeria else None,
            "produccion": dict(prod) if prod else None,
            "peso_molde": peso_molde,
        }
    finally:
        conn.close()


# ── Stock ─────────────────────────────────────────────────────────────────────

@app.get("/api/stock")
def get_stock(
    search:  Optional[str] = Query(None),
    cliente: Optional[str] = Query(None),
):
    conn = get_db()
    try:
        wp: list[str] = []
        pa: list      = []
        if cliente:
            wp.append("p.códigocliente = ?")
            pa.append(cliente)
        if search:
            s = f"%{search.strip()}%"
            wp.append(
                "(np.nombrepieza LIKE ? OR np.códigopiezapuestoporcliente LIKE ?"
                " OR COALESCE(c.nombrefantasía, c.nombrecliente) LIKE ?)"
            )
            pa.extend([s, s, s])

        extra = (" AND " + " AND ".join(wp)) if wp else ""

        rows = conn.execute(f"""
            SELECT
                np.id                                                  AS pieza_id,
                np.nombrepieza                                         AS nombre,
                np.códigopiezapuestoporcliente                         AS codigo,
                COALESCE(c.nombrefantasía, c.nombrecliente)            AS cliente,
                c.códigocliente                                        AS cliente_codigo,
                np.pesoestablecido                                     AS peso,
                COALESCE(SUM(t.cantidadenviadastock), 0)               AS enviado,
                COALESCE(SUM(t.cantidaddestock),      0)               AS consumido,
                COALESCE(SUM(ms_agg.ajuste_total),    0)               AS ajustes,
                COALESCE(SUM(t.cantidadenviadastock), 0)
                - COALESCE(SUM(t.cantidaddestock),    0)
                + COALESCE(SUM(ms_agg.ajuste_total),  0)               AS stock_actual,
                MAX(CASE WHEN t.cantidadenviadastock > 0
                         THEN t.ubicaciónstock END)                    AS ubicacion
            FROM Trabajos t
            JOIN ItemDetallePedido idp ON t.iditempedido  = idp.iditempedido
            JOIN Pedidos p             ON idp.idpedido    = p.idpedido
            JOIN Clientes c            ON p.códigocliente = c.códigocliente
            JOIN NombreDePiezas np     ON np.id           = idp.idpieza
            LEFT JOIN (
                SELECT iditemtrabajo, SUM(ajuste) AS ajuste_total
                FROM ModificacionesStock
                GROUP BY iditemtrabajo
            ) ms_agg ON ms_agg.iditemtrabajo = t.iditemtrabajo
            WHERE (t.cantidadenviadastock > 0 OR t.cantidaddestock > 0){extra}
            GROUP BY np.id, c.códigocliente
            HAVING stock_actual > 0
            ORDER BY stock_actual DESC
        """, pa).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            peso = r["peso"]
            d["kg_stock"] = round(peso * r["stock_actual"], 1) if peso and peso > 0 else None
            result.append(d)

        return {
            "items":          result,
            "total_unidades": sum(r["stock_actual"] for r in rows),
            "total_piezas":   len(result),
        }
    finally:
        conn.close()


# ── Frontend ──────────────────────────────────────────────────────────────────

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi.responses import Response
    return Response(status_code=204)


@app.get("/")
def serve_index():
    index = FRONTEND / "index.html"
    if not index.exists():
        return JSONResponse({"error": "Frontend no encontrado"}, 404)
    return FileResponse(index, headers={"Cache-Control": "no-store"})


# ── Entry point ───────────────────────────────────────────────────────────────

def _win_job_kill_on_close() -> None:
    """Assign this process to a Windows Job Object with KILL_ON_JOB_CLOSE.
    The OS automatically kills every child in the job when this process exits."""
    import ctypes

    class _BASIC(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit",     ctypes.c_longlong),
            ("LimitFlags",             ctypes.c_ulong),
            ("MinimumWorkingSetSize",  ctypes.c_size_t),
            ("MaximumWorkingSetSize",  ctypes.c_size_t),
            ("ActiveProcessLimit",     ctypes.c_ulong),
            ("Affinity",               ctypes.c_size_t),
            ("PriorityClass",          ctypes.c_ulong),
            ("SchedulingClass",        ctypes.c_ulong),
        ]

    class _IO(ctypes.Structure):
        _fields_ = [(f, ctypes.c_ulonglong) for f in
                    ("Read","Write","Other","ReadBytes","WriteBytes","OtherBytes")]

    class _EXT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC),
            ("IoInfo",                _IO),
            ("ProcessMemoryLimit",    ctypes.c_size_t),
            ("JobMemoryLimit",        ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed",     ctypes.c_size_t),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    hjob = k32.CreateJobObjectW(None, None)
    if not hjob:
        return
    ext = _EXT()
    ext.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    k32.SetInformationJobObject(hjob, 9, ctypes.byref(ext), ctypes.sizeof(ext))
    k32.AssignProcessToJobObject(hjob, k32.GetCurrentProcess())
    # hjob is intentionally kept open — the OS closes it (and kills all children) on exit


def _free_port(port: int) -> None:
    """Kill every process that owns the socket on the given port, then wait until bindable."""
    import time
    import socket
    try:
        # Get-NetTCPConnection returns the ACTUAL owning PID (not the original creator).
        # netstat -ano shows the PID that created the socket, which may already be dead
        # while a child process has inherited and still holds it open.
        ps = (
            f"(Get-NetTCPConnection -LocalPort {port} -State Listen "
            f"-ErrorAction SilentlyContinue).OwningProcess"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True
        )
        pids: set[int] = set()
        for line in result.stdout.strip().splitlines():
            try:
                pids.add(int(line.strip()))
            except ValueError:
                pass
        for pid in pids:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
            log.info("Killed process tree %d on port %d", pid, port)
        # Wait until the port is actually bindable (up to 5 s)
        for _ in range(50):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("0.0.0.0", port))
                    break
                except OSError:
                    pass
            time.sleep(0.1)
    except Exception as e:
        log.warning("Could not free port %d: %s", port, e)


# ═══════════════════════════════════════════════════════════════════════════════
# MÓDULO PROVEEDORES ISO 9001
# ═══════════════════════════════════════════════════════════════════════════════

import unicodedata as _uc
from fastapi import UploadFile, File, Form

# ── Business logic (portado de business_logic.py de provedores pyton) ─────────

def _pv_normalize_text(value):
    text = str(value or "").strip().lower()
    text = "".join(ch for ch in _uc.normalize("NFD", text) if _uc.category(ch) != "Mn")
    return " ".join(text.replace(".", "").split())

def _pv_normalize_unit(value):
    raw = str(value or "").strip()
    lower = raw.lower()
    if lower in ("kg", "k", "kilo", "kilos"):      return "Kg"
    if lower in ("tn", "t", "ton", "tonelada", "toneladas"): return "Tn"
    if lower in ("un", "u", "unidad", "unidades"):  return "Un"
    if lower in ("l", "lt", "lts", "litro", "litros"): return "L"
    return raw or "Kg"

def _pv_guess_unit(product_name):
    name = product_name.lower()
    if any(w in name for w in ("diluyente","alcohol","liquido","líquido","aceite")): return "L"
    if any(w in name for w in ("probeta","probetero","cuchara","manguito","kalpur","noyo","pieza")): return "Un"
    return "Kg"

def _pv_safe_file(value):
    text = _pv_normalize_text(value).replace(" ", "_")
    return "".join(ch for ch in text if ch.isalnum() or ch in ("_","-")) or "sin_dato"

def _pv_split_multi(value):
    raw = str(value or "").strip()
    if not raw:
        return []
    parts = []
    for chunk in raw.replace("\n", ";").split(";"):
        text = chunk.strip()
        if text and text not in parts:
            parts.append(text)
    return parts

def _pv_parse_num(value):
    text = str(value or "").strip().replace(",", ".")
    if not text or text == "-":
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0

def _pv_parse_date(value):
    from datetime import date as _date, timedelta as _td
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            from datetime import datetime as _dt
            return _dt.strptime(text, fmt).date()
        except ValueError:
            pass
    try:
        serial = float(text)
        return _date(1899, 12, 30) + _td(days=serial)
    except ValueError:
        return None

def _pv_fmt_num(value):
    if abs(value - int(value)) < 0.000001:
        return str(int(value))
    return ("%.2f" % value).rstrip("0").rstrip(".")

def _pv_recalc_quality(provider_rows, psp_rows, today=None):
    """Recalcula nivel de calidad. provider_rows y psp_rows son dicts."""
    from datetime import date as _date, timedelta as _td
    today = today or _date.today()
    start = today - _td(days=365)
    next_review = _date(today.year + 1, 1, 1)
    cond_review = next_review - _td(days=182)
    updated = []
    for prov in provider_rows:
        name = prov["nombre"].strip()
        groups = set(_pv_split_multi(prov.get("grupos", "")))
        received = observed = test_qty = 0.0
        for psp in psp_rows:
            if _pv_normalize_text(psp["proveedor"]) != _pv_normalize_text(name):
                continue
            if groups and psp.get("grupo","") not in groups:
                continue
            psp_date = _pv_parse_date(psp["fecha"])
            if not psp_date or psp_date < start or psp_date > today:
                continue
            rec = _pv_parse_num(psp["cant_recibida"])
            obs = sum(_pv_parse_num(v) for v in _pv_split_multi(psp.get("cant_observada","")))
            mot = _pv_normalize_text(psp.get("motivo",""))
            received += rec
            observed += obs
            if mot == "prueba":
                test_qty += obs
        row = dict(prov)
        row["cant_recibida"]  = _pv_fmt_num(received)
        row["cant_observada"] = _pv_fmt_num(observed)
        row["cant_prueba"]    = _pv_fmt_num(test_qty)
        if received == test_qty and received != 0:
            row["nivel"] = "A PRUEBA"
            row["valoracion"] = "1"
            row["vencimiento"] = "-"
        elif received == 0:
            row["nivel"] = "sin datos en el ultimo año"
            row["valoracion"] = "#DIV/0!"
            row["vencimiento"] = "-"
        else:
            val = (received - observed) / received
            pct = _pv_fmt_num(val * 100)
            row["valoracion"] = _pv_fmt_num(val)
            if val > 0.7:
                row["nivel"] = "APROBADO %s%%" % pct
                row["vencimiento"] = next_review.strftime("%d/%m/%Y")
            elif val > 0.4:
                row["nivel"] = "CONDICIONAL %s%%" % pct
                row["vencimiento"] = cond_review.strftime("%d/%m/%Y")
            else:
                row["nivel"] = "RECHAZADO %s%%" % pct
                row["vencimiento"] = "-"
        updated.append(row)
    return updated


def _pv_persist_quality(conn, updated_providers):
    """Escribe los campos calculados de calidad en _prov_proveedores."""
    for p in updated_providers:
        conn.execute(
            "UPDATE _prov_proveedores SET nivel=?, valoracion=?, cant_recibida=?, "
            "cant_observada=?, cant_prueba=?, vencimiento=? WHERE id=?",
            (p["nivel"], p["valoracion"], p["cant_recibida"],
             p["cant_observada"], p["cant_prueba"], p["vencimiento"], p["id"])
        )


def _pv_require_prov(user: dict = Depends(_get_auth_user)) -> dict:
    conn = sqlite3.connect(DB_PATH)
    try:
        secs = [r[0] for r in conn.execute(
            "SELECT seccion FROM _permisos WHERE legajo=?", (user["legajo"],)
        ).fetchall()]
    finally:
        conn.close()
    if "proveedores" not in secs and not user["is_admin"]:
        raise HTTPException(403, "Sin permiso para módulo Proveedores")
    return user


# ── Helpers compartidos ────────────────────────────────────────────────────────

def _pv_all_providers(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM _prov_proveedores ORDER BY nombre"
    ).fetchall()]

def _pv_all_psps(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM _prov_psp ORDER BY fecha DESC"
    ).fetchall()]


# ── Grupos ─────────────────────────────────────────────────────────────────────

@app.get("/api/prov/grupos")
def prov_get_grupos(user: dict = Depends(_pv_require_prov)):
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, nombre FROM _prov_grupos ORDER BY CAST(id AS INTEGER)"
        ).fetchall()]
    finally:
        conn.close()


@app.post("/api/prov/grupos")
async def prov_create_grupo(req: Request, user: dict = Depends(_pv_require_prov)):
    body = await req.json()
    gid  = str(body.get("id","")).strip()
    nombre = str(body.get("nombre","")).strip()
    if not gid or not nombre:
        raise HTTPException(400, "id y nombre son requeridos")
    conn = get_db()
    try:
        conn.execute("INSERT INTO _prov_grupos (id, nombre) VALUES (?,?)", (gid, nombre))
        conn.commit()
        return {"id": gid, "nombre": nombre}
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Ya existe un grupo con id {gid}")
    finally:
        conn.close()


@app.put("/api/prov/grupos/{gid}")
async def prov_update_grupo(gid: str, req: Request, user: dict = Depends(_pv_require_prov)):
    body = await req.json()
    nombre = str(body.get("nombre","")).strip()
    if not nombre:
        raise HTTPException(400, "nombre es requerido")
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM _prov_grupos WHERE id=?", (gid,)).fetchone():
            raise HTTPException(404, "Grupo no encontrado")
        conn.execute("UPDATE _prov_grupos SET nombre=? WHERE id=?", (nombre, gid))
        conn.commit()
        return {"id": gid, "nombre": nombre}
    finally:
        conn.close()


@app.delete("/api/prov/grupos/{gid}")
def prov_delete_grupo(gid: str, admin: dict = Depends(_require_admin)):
    conn = get_db()
    try:
        conn.execute("DELETE FROM _prov_grupos WHERE id=?", (gid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ── Unidades ───────────────────────────────────────────────────────────────────

@app.get("/api/prov/unidades")
def prov_get_unidades(user: dict = Depends(_pv_require_prov)):
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, descripcion FROM _prov_unidades ORDER BY id"
        ).fetchall()]
    finally:
        conn.close()


@app.post("/api/prov/unidades")
async def prov_create_unidad(req: Request, user: dict = Depends(_pv_require_prov)):
    body = await req.json()
    uid  = _pv_normalize_unit(str(body.get("id","")).strip())
    desc = str(body.get("descripcion","")).strip()
    if not uid:
        raise HTTPException(400, "id es requerido")
    conn = get_db()
    try:
        conn.execute("INSERT INTO _prov_unidades (id, descripcion) VALUES (?,?)", (uid, desc))
        conn.commit()
        return {"id": uid, "descripcion": desc}
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Ya existe la unidad {uid}")
    finally:
        conn.close()


@app.delete("/api/prov/unidades/{uid}")
def prov_delete_unidad(uid: str, admin: dict = Depends(_require_admin)):
    conn = get_db()
    try:
        conn.execute("DELETE FROM _prov_unidades WHERE id=?", (uid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ── Proveedores ────────────────────────────────────────────────────────────────

@app.get("/api/prov/proveedores")
def prov_get_proveedores(
    eval_date: Optional[str] = Query(None),
    recalc: bool = Query(False),
    user: dict = Depends(_pv_require_prov),
):
    conn = get_db()
    try:
        providers = _pv_all_providers(conn)
        if recalc:
            psps = _pv_all_psps(conn)
            today = None
            if eval_date:
                try:
                    from datetime import date as _date
                    today = _date.fromisoformat(eval_date)
                except ValueError:
                    pass
            updated = _pv_recalc_quality(providers, psps, today)
            # Persist only if using today's date (real recalc)
            if not eval_date:
                _pv_persist_quality(conn, updated)
                conn.commit()
            return updated
        return providers
    finally:
        conn.close()


@app.post("/api/prov/proveedores")
async def prov_create_proveedor(req: Request, user: dict = Depends(_pv_require_prov)):
    body = await req.json()
    nombre = str(body.get("nombre","")).strip()
    if not nombre:
        raise HTTPException(400, "nombre es requerido")
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO _prov_proveedores (nombre, rubros, grupos, observaciones) VALUES (?,?,?,?)",
            (nombre,
             str(body.get("rubros","")),
             str(body.get("grupos","")),
             str(body.get("observaciones","")))
        )
        conn.commit()
        return {"id": cur.lastrowid, "nombre": nombre}
    finally:
        conn.close()


@app.put("/api/prov/proveedores/{pid}")
async def prov_update_proveedor(pid: int, req: Request, user: dict = Depends(_pv_require_prov)):
    body = await req.json()
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM _prov_proveedores WHERE id=?", (pid,)).fetchone():
            raise HTTPException(404, "Proveedor no encontrado")
        fields = {k: body[k] for k in ("nombre","rubros","grupos","observaciones") if k in body}
        if not fields:
            raise HTTPException(400, "Sin campos para actualizar")
        set_clause = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE _prov_proveedores SET {set_clause} WHERE id=?",
                     list(fields.values()) + [pid])
        conn.commit()
        return dict(conn.execute("SELECT * FROM _prov_proveedores WHERE id=?", (pid,)).fetchone())
    finally:
        conn.close()


@app.delete("/api/prov/proveedores/{pid}")
def prov_delete_proveedor(pid: int, admin: dict = Depends(_require_admin)):
    conn = get_db()
    try:
        conn.execute("DELETE FROM _prov_proveedores WHERE id=?", (pid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/prov/proveedores/print")
def prov_print_proveedores(
    eval_date: Optional[str] = Query(None),
    solo_aprobados: bool = Query(False),
    user: dict = Depends(_pv_require_prov),
):
    conn = get_db()
    try:
        providers = _pv_all_providers(conn)
        psps      = _pv_all_psps(conn)
    finally:
        conn.close()
    today = None
    if eval_date:
        try:
            from datetime import date as _date
            today = _date.fromisoformat(eval_date)
        except ValueError:
            pass
    rows = _pv_recalc_quality(providers, psps, today)
    if solo_aprobados:
        rows = [r for r in rows if str(r.get("nivel","")).upper().startswith("APROBADO")]
    from datetime import date as _date
    eval_str = (today or _date.today()).strftime("%d/%m/%Y")

    def _nivel_color(nivel):
        n = nivel.lower()
        if "aprobado" in n:
            val_part = nivel.replace("APROBADO","").replace("%","").strip()
            try:
                if float(val_part) >= 100:
                    return "#2e7d32", "#e8f5e9"  # badge, row
            except Exception:
                pass
            return "#1b5e20", "#f9fbe7"
        if "condicional" in n: return "#0d47a1", "#e3f2fd"
        if "rechazado"   in n: return "#b71c1c", "#ffebee"
        return "#424242", "#f5f5f5"

    cols = ["nombre","rubros","nivel","vencimiento","cant_recibida","cant_observada","cant_prueba","observaciones"]
    labels = ["Proveedor","Rubro","Nivel de calidad","Próx. revisión","Cant. recibida","Cant. observada","Cant. prueba","Observaciones"]

    rows_html = ""
    for r in rows:
        badge_col, row_col = _nivel_color(r.get("nivel",""))
        cells = ""
        for i, c in enumerate(cols):
            val = r.get(c,"")
            if c == "nivel":
                cells += f'<td><span style="background:{badge_col};color:#fff;padding:2px 8px;border-radius:3px;font-size:0.85em">{val}</span></td>'
            else:
                cells += f"<td>{val}</td>"
        rows_html += f'<tr style="background:{row_col}">{cells}</tr>\n'

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Proveedores — {eval_str}</title>
<style>
@page{{size:A4 landscape;margin:12mm}}
*{{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important;font-family:Arial,sans-serif;font-size:11px}}
body{{margin:0;padding:0;background:#fff}}
.hdr{{background:#263238;color:#fff;padding:8px 12px;margin-bottom:0;border-bottom:4px solid #c62828}}
.hdr h1{{margin:0;font-size:16px}}
.hdr p{{margin:2px 0;font-size:10px;opacity:.8}}
table{{width:100%;border-collapse:collapse}}
th{{background:#37474f;color:#fff;padding:4px 6px;text-align:left}}
td{{padding:3px 6px;border-bottom:1px solid #e0e0e0;vertical-align:top}}
@media print{{*{{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}}}}
</style></head><body>
<div class="hdr"><h1>Listado de Proveedores — Nivel de Calidad</h1>
<p>Período evaluado al: {eval_str} &nbsp;|&nbsp; Generado: {_date.today().strftime("%d/%m/%Y")}{" &nbsp;|&nbsp; Solo aprobados" if solo_aprobados else ""}</p></div>
<table><thead><tr>{''.join(f"<th>{l}</th>" for l in labels)}</tr></thead>
<tbody>{rows_html}</tbody></table>
</body></html>"""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(html)


# ── Productos ──────────────────────────────────────────────────────────────────

@app.get("/api/prov/productos")
def prov_get_productos(
    proveedor_id: Optional[int] = Query(None),
    grupo: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    user: dict = Depends(_pv_require_prov),
):
    conn = get_db()
    try:
        q = "SELECT * FROM _prov_productos WHERE 1=1"
        params: list = []
        if proveedor_id:
            q += " AND proveedor_id=?"
            params.append(proveedor_id)
        if grupo:
            q += " AND grupo=?"
            params.append(grupo)
        if search:
            q += " AND (nombre LIKE ? OR proveedor_nombre LIKE ? OR grupo LIKE ?)"
            s = f"%{search}%"
            params += [s, s, s]
        q += " ORDER BY nombre"
        return [dict(r) for r in conn.execute(q, params).fetchall()]
    finally:
        conn.close()


@app.post("/api/prov/productos")
async def prov_create_producto(req: Request, user: dict = Depends(_pv_require_prov)):
    body = await req.json()
    nombre = str(body.get("nombre","")).strip()
    if not nombre:
        raise HTTPException(400, "nombre es requerido")
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO _prov_productos (nombre,descripcion,rubro,grupo,proveedor_id,"
            "proveedor_nombre,cantidad_ref,etp,critico,unidad) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (nombre,
             str(body.get("descripcion","")),
             str(body.get("rubro","")),
             str(body.get("grupo","")),
             body.get("proveedor_id"),
             str(body.get("proveedor_nombre","")),
             str(body.get("cantidad_ref","")),
             str(body.get("etp","")),
             1 if body.get("critico") else 0,
             _pv_normalize_unit(str(body.get("unidad","Kg"))))
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM _prov_productos WHERE id=?", (cur.lastrowid,)).fetchone())
    finally:
        conn.close()


@app.put("/api/prov/productos/{pid}")
async def prov_update_producto(pid: int, req: Request, user: dict = Depends(_pv_require_prov)):
    body = await req.json()
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM _prov_productos WHERE id=?", (pid,)).fetchone():
            raise HTTPException(404, "Producto no encontrado")
        allowed = ("nombre","descripcion","rubro","grupo","proveedor_id",
                   "proveedor_nombre","cantidad_ref","etp","critico","unidad")
        fields = {k: body[k] for k in allowed if k in body}
        if "unidad" in fields:
            fields["unidad"] = _pv_normalize_unit(str(fields["unidad"]))
        if "critico" in fields:
            fields["critico"] = 1 if fields["critico"] else 0
        if not fields:
            raise HTTPException(400, "Sin campos para actualizar")
        set_clause = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE _prov_productos SET {set_clause} WHERE id=?",
                     list(fields.values()) + [pid])
        conn.commit()
        return dict(conn.execute("SELECT * FROM _prov_productos WHERE id=?", (pid,)).fetchone())
    finally:
        conn.close()


@app.delete("/api/prov/productos/{pid}")
def prov_delete_producto(pid: int, admin: dict = Depends(_require_admin)):
    conn = get_db()
    try:
        conn.execute("DELETE FROM _prov_productos WHERE id=?", (pid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ── ETP ────────────────────────────────────────────────────────────────────────

@app.get("/api/prov/etp")
def prov_get_etp(user: dict = Depends(_pv_require_prov)):
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT codigo,producto,revision,estado,detalle,pdf_path FROM _prov_etp ORDER BY codigo"
        ).fetchall()]
    finally:
        conn.close()


@app.post("/api/prov/etp")
async def prov_create_etp(req: Request, user: dict = Depends(_pv_require_prov)):
    body = await req.json()
    codigo = str(body.get("codigo","")).strip()
    if not codigo:
        raise HTTPException(400, "codigo es requerido")
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO _prov_etp (codigo,producto,revision,estado,detalle,pdf_path) VALUES (?,?,?,?,?,?)",
            (codigo,
             str(body.get("producto","")),
             str(body.get("revision","")),
             str(body.get("estado","Vigente")),
             str(body.get("detalle","")),
             str(body.get("pdf_path","")))
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM _prov_etp WHERE codigo=?", (codigo,)).fetchone())
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Ya existe ETP {codigo}")
    finally:
        conn.close()


@app.put("/api/prov/etp/{codigo}")
async def prov_update_etp(codigo: str, req: Request, user: dict = Depends(_pv_require_prov)):
    body = await req.json()
    conn = get_db()
    try:
        if not conn.execute("SELECT codigo FROM _prov_etp WHERE codigo=?", (codigo,)).fetchone():
            raise HTTPException(404, "ETP no encontrada")
        allowed = ("producto","revision","estado","detalle","pdf_path")
        fields = {k: body[k] for k in allowed if k in body}
        if not fields:
            raise HTTPException(400, "Sin campos para actualizar")
        set_clause = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE _prov_etp SET {set_clause} WHERE codigo=?",
                     list(fields.values()) + [codigo])
        conn.commit()
        return dict(conn.execute("SELECT * FROM _prov_etp WHERE codigo=?", (codigo,)).fetchone())
    finally:
        conn.close()


@app.delete("/api/prov/etp/{codigo}")
def prov_delete_etp(codigo: str, admin: dict = Depends(_require_admin)):
    conn = get_db()
    try:
        conn.execute("DELETE FROM _prov_etp WHERE codigo=?", (codigo,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/prov/etp/{codigo}/pdf")
def prov_etp_pdf(codigo: str, user: dict = Depends(_pv_require_prov)):
    conn = get_db()
    try:
        row = conn.execute("SELECT pdf_path FROM _prov_etp WHERE codigo=?", (codigo,)).fetchone()
    finally:
        conn.close()
    if not row or not row["pdf_path"]:
        raise HTTPException(404, "PDF no disponible")
    path = Path(row["pdf_path"])
    if not path.is_absolute():
        path = ROOT / "data" / row["pdf_path"]
    if not path.exists():
        raise HTTPException(404, "Archivo PDF no encontrado en disco")
    return FileResponse(str(path), media_type="application/pdf",
                        filename=path.name)


# ── PSP ────────────────────────────────────────────────────────────────────────

def _pv_recalc_and_save(conn):
    """Recalcula y persiste niveles de todos los proveedores."""
    providers = _pv_all_providers(conn)
    psps      = _pv_all_psps(conn)
    updated   = _pv_recalc_quality(providers, psps)
    _pv_persist_quality(conn, updated)


@app.get("/api/prov/psp")
def prov_get_psp(
    proveedor:    Optional[str] = Query(None),
    producto:     Optional[str] = Query(None),
    fecha_desde:  Optional[str] = Query(None),
    fecha_hasta:  Optional[str] = Query(None),
    limit:        int           = Query(500),
    user: dict = Depends(_pv_require_prov),
):
    conn = get_db()
    try:
        q = "SELECT * FROM _prov_psp WHERE 1=1"
        params: list = []
        if proveedor:
            q += " AND lower(proveedor) LIKE lower(?)"
            params.append(f"%{proveedor}%")
        if producto:
            q += " AND lower(producto) LIKE lower(?)"
            params.append(f"%{producto}%")
        if fecha_desde:
            q += " AND fecha >= ?"
            params.append(fecha_desde)
        if fecha_hasta:
            q += " AND fecha <= ?"
            params.append(fecha_hasta)
        q += " ORDER BY fecha DESC, id DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(q, params).fetchall()]
    finally:
        conn.close()


@app.get("/api/prov/psp/{psp_id}")
def prov_get_psp_one(psp_id: int, user: dict = Depends(_pv_require_prov)):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM _prov_psp WHERE id=?", (psp_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Registro PSP no encontrado")
        return dict(row)
    finally:
        conn.close()


@app.post("/api/prov/psp")
async def prov_create_psp(req: Request, user: dict = Depends(_pv_require_prov)):
    body = await req.json()
    producto  = str(body.get("producto","")).strip()
    proveedor = str(body.get("proveedor","")).strip()
    fecha     = str(body.get("fecha","")).strip()
    if not producto or not proveedor or not fecha:
        raise HTTPException(400, "producto, proveedor y fecha son requeridos")
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO _prov_psp (producto,proveedor,grupo,etp,fecha,cant_recibida,unidad,"
            "cant_observada,motivo,tiene_remito,control_visual,tiene_informe,"
            "partida,remito,observaciones,archivo_informe,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (producto, proveedor,
             str(body.get("grupo","")),
             str(body.get("etp","")),
             fecha,
             _pv_parse_num(body.get("cant_recibida",0)),
             _pv_normalize_unit(str(body.get("unidad","Kg"))),
             str(body.get("cant_observada","-")),
             str(body.get("motivo","-")),
             1 if body.get("tiene_remito", True) else 0,
             1 if body.get("control_visual", True) else 0,
             1 if body.get("tiene_informe", False) else 0,
             str(body.get("partida","")),
             str(body.get("remito","")),
             str(body.get("observaciones","")),
             "",
             datetime.now().isoformat())
        )
        new_id = cur.lastrowid
        _pv_recalc_and_save(conn)
        conn.commit()
        return dict(conn.execute("SELECT * FROM _prov_psp WHERE id=?", (new_id,)).fetchone())
    finally:
        conn.close()


@app.put("/api/prov/psp/{psp_id}")
async def prov_update_psp(psp_id: int, req: Request, user: dict = Depends(_pv_require_prov)):
    body = await req.json()
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM _prov_psp WHERE id=?", (psp_id,)).fetchone():
            raise HTTPException(404, "Registro PSP no encontrado")
        allowed = ("producto","proveedor","grupo","etp","fecha","cant_recibida","unidad",
                   "cant_observada","motivo","tiene_remito","control_visual","tiene_informe",
                   "partida","remito","observaciones")
        fields: dict = {}
        for k in allowed:
            if k not in body:
                continue
            if k == "unidad":
                fields[k] = _pv_normalize_unit(str(body[k]))
            elif k == "cant_recibida":
                fields[k] = _pv_parse_num(body[k])
            elif k in ("tiene_remito","control_visual","tiene_informe"):
                fields[k] = 1 if body[k] else 0
            else:
                fields[k] = str(body[k])
        if not fields:
            raise HTTPException(400, "Sin campos para actualizar")
        set_clause = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE _prov_psp SET {set_clause} WHERE id=?",
                     list(fields.values()) + [psp_id])
        _pv_recalc_and_save(conn)
        conn.commit()
        return dict(conn.execute("SELECT * FROM _prov_psp WHERE id=?", (psp_id,)).fetchone())
    finally:
        conn.close()


@app.delete("/api/prov/psp/{psp_id}")
def prov_delete_psp(psp_id: int, user: dict = Depends(_pv_require_prov)):
    conn = get_db()
    try:
        row = conn.execute("SELECT archivo_informe FROM _prov_psp WHERE id=?", (psp_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Registro PSP no encontrado")
        if row["archivo_informe"]:
            try:
                (INFORMES_PSP / row["archivo_informe"]).unlink(missing_ok=True)
            except Exception:
                pass
        conn.execute("DELETE FROM _prov_psp WHERE id=?", (psp_id,))
        _pv_recalc_and_save(conn)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/prov/psp/{psp_id}/informe")
async def prov_upload_informe(
    psp_id: int,
    file: UploadFile = File(...),
    partida: str = Form(""),
    user: dict = Depends(_pv_require_prov),
):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT producto, proveedor FROM _prov_psp WHERE id=?", (psp_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Registro PSP no encontrado")
        content = await file.read()
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(413, "Archivo demasiado grande (máximo 20 MB)")
        ext = Path(file.filename or "archivo.bin").suffix.lower() or ".bin"
        safe_prov = _pv_safe_file(row["proveedor"])
        safe_prod = _pv_safe_file(row["producto"])
        safe_part = _pv_safe_file(partida or str(psp_id))
        dest_dir  = INFORMES_PSP / safe_prov / safe_prod
        dest_dir.mkdir(parents=True, exist_ok=True)
        filename  = f"{safe_part}{ext}"
        dest_path = dest_dir / filename
        dest_path.write_bytes(content)
        rel_path  = f"{safe_prov}/{safe_prod}/{filename}"
        conn.execute(
            "UPDATE _prov_psp SET tiene_informe=1, partida=?, archivo_informe=? WHERE id=?",
            (partida or row[0], rel_path, psp_id)
        )
        conn.commit()
        return {"ok": True, "archivo_informe": rel_path}
    finally:
        conn.close()


@app.get("/api/prov/psp/{psp_id}/informe")
def prov_download_informe(psp_id: int, user: dict = Depends(_pv_require_prov)):
    conn = get_db()
    try:
        row = conn.execute("SELECT archivo_informe FROM _prov_psp WHERE id=?", (psp_id,)).fetchone()
    finally:
        conn.close()
    if not row or not row["archivo_informe"]:
        raise HTTPException(404, "Sin informe adjunto")
    path = INFORMES_PSP / row["archivo_informe"]
    if not path.exists():
        raise HTTPException(404, "Archivo no encontrado en disco")
    import mimetypes
    mt = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(str(path), media_type=mt, filename=path.name)


@app.delete("/api/prov/psp/{psp_id}/informe")
def prov_delete_informe(psp_id: int, user: dict = Depends(_pv_require_prov)):
    conn = get_db()
    try:
        row = conn.execute("SELECT archivo_informe FROM _prov_psp WHERE id=?", (psp_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Registro PSP no encontrado")
        if row["archivo_informe"]:
            p = INFORMES_PSP / row["archivo_informe"]
            if p.exists():
                p.unlink(missing_ok=True)
        conn.execute("UPDATE _prov_psp SET tiene_informe=0, archivo_informe='' WHERE id=?", (psp_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ── Relaciones (validación de integridad) ──────────────────────────────────────

@app.get("/api/prov/relaciones")
def prov_get_relaciones(user: dict = Depends(_pv_require_prov)):
    conn = get_db()
    try:
        providers = {
            str(r["id"]): r["nombre"]
            for r in conn.execute("SELECT id, nombre FROM _prov_proveedores").fetchall()
        }
        grupos = {
            r["id"]: r["nombre"]
            for r in conn.execute("SELECT id, nombre FROM _prov_grupos").fetchall()
        }
        productos = [dict(r) for r in conn.execute("SELECT * FROM _prov_productos").fetchall()]
    finally:
        conn.close()

    issues = []
    for p in productos:
        pid = p.get("proveedor_id")
        pnombre = p.get("proveedor_nombre","")
        if not pid:
            issues.append({"nivel":"Error","producto":p["nombre"],"detalle":"Sin proveedor asignado"})
            continue
        if str(pid) not in providers:
            issues.append({"nivel":"Error","producto":p["nombre"],
                           "proveedor_id":pid,
                           "detalle":f"Proveedor id={pid} no existe"})
        elif pnombre and _pv_normalize_text(pnombre) != _pv_normalize_text(providers[str(pid)]):
            issues.append({"nivel":"Aviso","producto":p["nombre"],
                           "proveedor_id":pid,
                           "detalle":f"Nombre proveedor no coincide: '{pnombre}' vs '{providers[str(pid)]}'"})
        grupo = p.get("grupo","")
        if grupo and grupo not in grupos:
            issues.append({"nivel":"Aviso","producto":p["nombre"],
                           "detalle":f"Grupo '{grupo}' no existe en tabla de grupos"})
    return {"issues": issues, "total": len(issues)}


if __name__ == "__main__":
    _win_job_kill_on_close()
    _free_port(50504)
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=50504,
        reload=False,
        log_level="info",
    )
