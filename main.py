"""main.py — GNC API: FastAPI + sync Access → SQLite + frontend."""
import asyncio
import json
import logging
import sqlite3
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

ROOT       = Path(__file__).parent
DB_PATH    = ROOT / "data" / "gnc.db"
EXPORTS    = ROOT / "data" / "exports"
FRONTEND   = ROOT / "frontend"
PS32       = r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
PS_SCRIPT  = ROOT / "sync_access.ps1"

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
}


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

async def run_sync():
    import sync as sync_module

    sync_state["status"]   = "syncing"
    sync_state["error"]    = None
    sync_state["progress"] = {"done": 0, "total": 0, "phase": "Exportando desde Access..."}
    log.info("Iniciando sync Access → SQLite...")

    loop = asyncio.get_event_loop()

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
        return sync_module.sync_all(EXPORTS, DB_PATH, on_progress=_on_progress)

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


# ── DB helper ─────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(503, "Base de datos no disponible aún — sync en progreso")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    if _should_auto_sync():
        log.info("Auto-sync: iniciando (última sync hace ≥10 horas o primera vez)")
        asyncio.create_task(run_sync())
    else:
        log.info("Auto-sync: omitido (última sync hace <10 horas)")
        sync_state["status"] = "ready"
    yield


app = FastAPI(title="GNC API", version="0.1.0", lifespan=lifespan)


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


@app.get("/api/analytics/overview")
def analytics_overview():
    conn = get_db()
    try:
        año_actual = datetime.now().year

        # 5-year delivery trend via Pedidos.fechapedido (fechacargaot is NULL on ~85% of rows)
        trend_rows = conn.execute("""
            SELECT strftime('%Y', p.fechapedido) as año,
                   COALESCE(SUM(t.cantidadentregada), 0) as entregadas,
                   COALESCE(SUM(t.cantidadrechazada), 0) as rechazadas
            FROM Trabajos t
            JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido
            JOIN Pedidos p ON idp.idpedido = p.idpedido
            WHERE p.fechapedido >= ? AND t.cantidadentregada > 0
            GROUP BY año ORDER BY año
        """, (f"{año_actual - 4}-01-01",)).fetchall()

        # Returns by year
        dev_rows = conn.execute("""
            SELECT CAST(año AS TEXT) as año,
                   COALESCE(SUM("cantidaddevolución"), 0) as devueltas
            FROM "ItemDevolución"
            WHERE año >= ?
            GROUP BY año ORDER BY año
        """, (año_actual - 4,)).fetchall()
        dev_map = {r["año"]: r["devueltas"] for r in dev_rows}

        tendencia = []
        año_ent = año_dev = 0
        for r in trend_rows:
            dev = dev_map.get(r["año"], 0)
            tendencia.append({
                "año": r["año"],
                "entregadas": r["entregadas"],
                "rechazadas": r["rechazadas"],
                "devueltas": dev,
            })
            if r["año"] == str(año_actual):
                año_ent = r["entregadas"]
                año_dev = dev

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
            "top_clientes": [dict(r) for r in top],
            "n_tablas": n_tablas,
        }
    finally:
        conn.close()


@app.get("/api/analytics/clientes")
def analytics_clientes():
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT c.códigocliente as codigo,
                   c.nombrecliente as nombre,
                   COALESCE(c.nombrefantasía, c.nombrecliente) as fantasia,
                   c.e_mail_para_contacto as email,
                   COUNT(np.id) as n_piezas
            FROM Clientes c
            LEFT JOIN NombreDePiezas np ON np.códcliente = c.códigocliente
            GROUP BY c.códigocliente
            ORDER BY c.nombrecliente
        """).fetchall()
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

        # Annual deliveries via ItemDetallePedido → Pedidos
        # Use p.fechapedido as year source — fechacargaot is NULL on ~85% of rows
        ent_rows = conn.execute("""
            SELECT strftime('%Y', p.fechapedido) as año,
                   COALESCE(SUM(t.cantidadentregada), 0) as entregadas,
                   COALESCE(SUM(t.cantidadrechazada), 0) as rechazadas,
                   COALESCE(SUM(t.cantidadaprobada), 0) as aprobadas
            FROM Trabajos t
            JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido
            JOIN Pedidos p ON idp.idpedido = p.idpedido
            WHERE p.códigocliente = ? AND t.cantidadentregada > 0
              AND p.fechapedido IS NOT NULL
            GROUP BY año ORDER BY año
        """, (codigo,)).fetchall()

        # Annual returns: ItemDevolución → PesosDePiezas → NombreDePiezas
        dev_rows = conn.execute("""
            SELECT CAST(id.año AS TEXT) as año,
                   COALESCE(SUM(id."cantidaddevolución"), 0) as devueltas
            FROM "ItemDevolución" id
            JOIN PesosDePiezas pp ON id.códpieza = pp.códpieza
            JOIN NombreDePiezas np ON pp.nombredepiezasid_ = np.id
            WHERE np.códcliente = ?
            GROUP BY id.año ORDER BY id.año
        """, (codigo,)).fetchall()
        dev_map = {r["año"]: r["devueltas"] for r in dev_rows}

        anual = []
        total_ent = total_dev = 0
        for r in ent_rows:
            dev = dev_map.get(r["año"], 0)
            ent = r["entregadas"]
            total_ent += ent
            total_dev += dev
            anual.append({
                "año": r["año"],
                "entregadas": ent,
                "rechazadas": r["rechazadas"],
                "aprobadas": r["aprobadas"],
                "devueltas": dev,
                "tasa": round(dev / ent * 100, 2) if ent else 0,
            })

        # Pieces list with delivery + return totals
        piezas_rows = conn.execute("""
            SELECT np.id as pieza_id,
                   np.códigopiezapuestoporcliente as codigo_pieza,
                   np.nombrepieza as nombre,
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

        piezas = []
        for r in piezas_rows:
            ent = r["entregadas"]
            dev = dev_pieza.get(r["pieza_id"], 0)
            piezas.append({
                "pieza_id": r["pieza_id"],
                "codigo_pieza": r["codigo_pieza"],
                "nombre": r["nombre"],
                "entregadas": ent,
                "rechazadas": r["rechazadas"],
                "devueltas": dev,
                "tasa_devolucion": round(dev / ent * 100, 2) if ent else 0,
            })

        return {
            "cliente": dict(cl),
            "total_entregadas": total_ent,
            "total_devueltas": total_dev,
            "tasa_total": round(total_dev / total_ent * 100, 2) if total_ent else 0,
            "n_piezas": len(piezas),
            "anual": anual,
            "piezas": piezas,
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
                   nombrepieza as nombre
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
            "calidad":  _inf("InfTécnicaCalidad"),
            "rebaba":   _inf("InfTécnicaRebaba"),
            "noyeria":  _inf("InfTécnicaNoyería"),
            "moldeo":   _inf("InfTécnicaMoldeo"),
            "colada":   _inf("InfTécnicaColada"),
        }

        return {
            "pieza": dict(pieza),
            "anual": anual,
            "defectos": defectos,
            "inftecnica": inftecnica,
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

def _est_map(conn: sqlite3.Connection) -> dict:
    """Estado codes → labels."""
    return {
        r["códigoestado"]: r["leyendaestado"]
        for r in conn.execute(
            "SELECT códigoestado, leyendaestado FROM Estados GROUP BY códigoestado"
        ).fetchall()
    }


# ── Pedidos ───────────────────────────────────────────────────────────────────

@app.get("/api/pedidos")
def get_pedidos(
    año: Optional[int] = Query(None),
    cliente: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    conn = get_db()
    try:
        est = _est_map(conn)
        wp, pa = [], []
        if año:
            wp.append("strftime('%Y', p.fechapedido) = ?"); pa.append(str(año))
        if cliente:
            wp.append("p.códigocliente = ?"); pa.append(cliente)
        if estado:
            wp.append("p.estadopedido = ?"); pa.append(estado)
        if search:
            s = f"%{search.strip()}%"
            wp.append("(COALESCE(c.nombrefantasía, c.nombrecliente) LIKE ? OR CAST(p.nropedido AS TEXT) LIKE ? OR p.observaciones LIKE ?)")
            pa.extend([s, s, s])
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
                   COALESCE(SUM(t.cantidadproducida), 0) as producidas
            FROM ItemDetallePedido idp
            JOIN NombreDePiezas np ON idp.idpieza = np.id
            LEFT JOIN Trabajos t ON idp.iditempedido = t.iditempedido
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
            result_items.append(d)
        return {"pedido": pd_dict, "items": result_items}
    finally:
        conn.close()


# ── Remitos ───────────────────────────────────────────────────────────────────

@app.get("/api/remitos")
def get_remitos(
    año: Optional[int] = Query(None),
    cliente: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    conn = get_db()
    try:
        wp, pa = [], []
        if año:
            wp.append("strftime('%Y', r.fecharemito) = ?"); pa.append(str(año))
        if cliente:
            wp.append("r.códigocliente = ?"); pa.append(cliente)
        if search:
            s = f"%{search.strip()}%"
            wp.append("(COALESCE(c.nombrefantasía, c.nombrecliente) LIKE ? OR CAST(r.idnroremito AS TEXT) LIKE ? OR r.observaciones LIKE ?)")
            pa.extend([s, s, s])
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

        return {"remito": dict(r), "items": [dict(i) for i in items]}
    finally:
        conn.close()


# ── Piezas ────────────────────────────────────────────────────────────────────

@app.get("/api/piezas")
def get_piezas(
    cliente: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    habilitado: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    conn = get_db()
    try:
        wp, pa = [], []
        if cliente:
            wp.append("np.códcliente = ?"); pa.append(cliente)
        if search:
            s = f"%{search.strip()}%"
            wp.append("(np.nombrepieza LIKE ? OR np.códigopiezapuestoporcliente LIKE ?)")
            pa.extend([s, s])
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
    cliente: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    habilitado: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
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
            SELECT fechaoperación, operación, observaciones
            FROM MovimientoModelos WHERE códigomodelo = ?
            ORDER BY fechaoperación DESC LIMIT 30
        """, (modelo_id,)).fetchall()

        fotos_count = conn.execute(
            "SELECT COUNT(*) FROM FotosDeModelos WHERE códigomodelo = ? AND habilitada = 1",
            (modelo_id,)
        ).fetchone()[0]

        return {
            "modelo": dict(m),
            "piezas": [dict(r) for r in piezas],
            "movimientos": [dict(r) for r in movimientos],
            "fotos_count": fotos_count,
        }
    finally:
        conn.close()


# ── Fundiciones ───────────────────────────────────────────────────────────────

@app.get("/api/fundiciones")
def get_fundiciones(
    año: Optional[int] = Query(None),
    cerrada: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
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
        if search:
            wp.append("CAST(f.códfundición AS TEXT) LIKE ?"); pa.append(f"%{search.strip()}%")
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
        }
    finally:
        conn.close()


# ── Documentos ────────────────────────────────────────────────────────────────

@app.get("/api/documentos")
def get_documentos(
    año: Optional[int] = Query(None),
    cliente: Optional[str] = Query(None),
    tipo: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
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
        if search:
            s = f"%{search.strip()}%"
            wp.append("(d.descripción LIKE ? OR d.nrodoc LIKE ? OR COALESCE(c.nombrefantasía, c.nombrecliente) LIKE ?)")
            pa.extend([s, s, s])
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
    cliente: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    conn = get_db()
    try:
        est_desc = _est_map(conn)

        where_parts: list[str] = []
        params: list = []

        if año:
            where_parts.append("strftime('%Y', p.fechapedido) = ?")
            params.append(str(año))
        if cliente:
            where_parts.append("p.códigocliente = ?")
            params.append(cliente)
        if estado:
            where_parts.append("t.estadotrabajo = ?")
            params.append(estado)
        if search:
            s = f"%{search.strip()}%"
            where_parts.append(
                "(COALESCE(c.nombrefantasía, c.nombrecliente) LIKE ?"
                " OR np.nombrepieza LIKE ?"
                " OR CAST(t.obsot AS TEXT) LIKE ?)"
            )
            params.extend([s, s, s])

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
            f"""SELECT t.iditemtrabajo, p.fechapedido as fecha, t.fechaprevista,
                t.códfundición as fundicion, t.estadotrabajo,
                p.códigocliente as codigo_cliente,
                COALESCE(c.nombrefantasía, c.nombrecliente) as cliente_nombre,
                np.nombrepieza, np.códigopiezapuestoporcliente as codigo_pieza,
                t.cantidadproducir, t.cantidadproducida, t.cantidadrechazada,
                t.cantidadaprobada, t.cantidadentregada, t.obsot
            {base_from} {where}
            ORDER BY p.fechapedido DESC
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
    return FileResponse(index)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=50504,
        reload=False,
        log_level="info",
    )
