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

        # 5-year delivery trend (Trabajos grouped by year)
        trend_rows = conn.execute("""
            SELECT strftime('%Y', fechacargaot) as año,
                   COALESCE(SUM(cantidadentregada), 0) as entregadas,
                   COALESCE(SUM(cantidadrechazada), 0) as rechazadas
            FROM Trabajos
            WHERE fechacargaot >= ? AND fechacargaot IS NOT NULL
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
            WHERE t.fechacargaot >= ? AND t.cantidadentregada > 0
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
        ent_rows = conn.execute("""
            SELECT strftime('%Y', t.fechacargaot) as año,
                   COALESCE(SUM(t.cantidadentregada), 0) as entregadas,
                   COALESCE(SUM(t.cantidadrechazada), 0) as rechazadas,
                   COALESCE(SUM(t.cantidadaprobada), 0) as aprobadas
            FROM Trabajos t
            JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido
            JOIN Pedidos p ON idp.idpedido = p.idpedido
            WHERE p.códigocliente = ? AND t.fechacargaot IS NOT NULL
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

        # Annual delivery from Trabajos via ItemDetallePedido
        ent_rows = conn.execute("""
            SELECT strftime('%Y', t.fechacargaot) as año,
                   COALESCE(SUM(t.cantidadentregada), 0) as entregadas,
                   COALESCE(SUM(t.cantidadrechazada), 0) as rechazadas,
                   COALESCE(SUM(t.cantidadaprobada), 0) as aprobadas
            FROM Trabajos t
            JOIN ItemDetallePedido idp ON t.iditempedido = idp.iditempedido
            WHERE idp.idpieza = ? AND t.fechacargaot IS NOT NULL
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

        return {
            "pieza": dict(pieza),
            "anual": anual,
            "defectos": defectos,
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
