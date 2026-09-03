"""kiosk_server.py — Servidor kiosk dedicado en puerto 50505 con proxy de API."""
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import httpx
import uvicorn

FRONTEND = Path(__file__).parent / "frontend"
API_ORIGIN = "http://127.0.0.1:50504"

app = FastAPI(title="GNC Kiosk")

_client = httpx.AsyncClient(base_url=API_ORIGIN, timeout=30.0)


@app.get("/")
def root():
    return FileResponse(FRONTEND / "kiosk.html", headers={"Cache-Control": "no-store"})


_HOP_BY_HOP = {
    "connection", "keep-alive", "transfer-encoding", "te",
    "trailer", "upgrade", "proxy-authorization", "proxy-authenticate",
    "content-encoding", "content-length",
}

@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_api(path: str, request: Request):
    url = f"/api/{path}"
    qs = request.url.query
    if qs:
        url = f"{url}?{qs}"
    body = await request.body()
    req_headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in ("host", "content-length", "content-encoding")}
    try:
        r = await _client.request(request.method, url, content=body, headers=req_headers)
    except Exception as e:
        return Response(content=str(e).encode(), status_code=502)
    resp_headers = {k: v for k, v in r.headers.items()
                    if k.lower() not in _HOP_BY_HOP}
    return Response(content=r.content, status_code=r.status_code, headers=resp_headers)


_vendor = FRONTEND / "vendor"
if _vendor.exists():
    app.mount("/vendor", StaticFiles(directory=str(_vendor)), name="vendor")

# Mismos frontend/js/ y frontend/css/ que sirve main.py -- una sola fuente en
# disco, montada aca tambien porque kiosk_server corre en su propio proceso/
# puerto (50505).
_js = FRONTEND / "js"
if _js.exists():
    app.mount("/js", StaticFiles(directory=str(_js)), name="js")
_css = FRONTEND / "css"
if _css.exists():
    app.mount("/css", StaticFiles(directory=str(_css)), name="css")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=50505, log_level="info")
