import os
import sys
import json
import shutil
import zipfile
import tempfile
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

ROOT     = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
VENV_PY  = ROOT / ".venv" / "Scripts" / "python.exe"
SHA_FILE = ROOT / "_version.txt"

GITHUB_REPO   = "ledesmamatiasarispe/estadisticasRomero"
GITHUB_BRANCH = "master"

SKIP_UPDATE = {".venv", "iniciar.exe", "iniciar.bat", "_version.txt", "data"}


# ── Búsqueda de Python del sistema ────────────────────────────────────────────

def find_python():
    py = shutil.which("py")
    if py:
        r = subprocess.run([py, "-3", "--version"], capture_output=True)
        if r.returncode == 0:
            return [py, "-3"]

    python = shutil.which("python")
    if python:
        r = subprocess.run([python, "--version"], capture_output=True, text=True)
        if r.returncode == 0 and "Python 3" in (r.stdout + r.stderr):
            return [python]

    python3 = shutil.which("python3")
    if python3:
        return [python3]

    bases = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python",
        Path(os.environ.get("ProgramFiles", "")),
        Path(os.environ.get("ProgramFiles(x86)", "")),
        Path("C:/"),
    ]
    for base in bases:
        if base.exists():
            for d in sorted(base.glob("Python3*"), reverse=True):
                exe = d / "python.exe"
                if exe.exists():
                    return [str(exe)]

    return None


def abort(msg):
    print(f"\n [ERROR] {msg}\n")
    input(" Presiona Enter para cerrar...")
    sys.exit(1)


# ── Auto-actualización desde GitHub (sin Git) ─────────────────────────────────

def _remote_sha():
    url = f"https://api.github.com/repos/{GITHUB_REPO}/commits/{GITHUB_BRANCH}"
    req = urllib.request.Request(url, headers={"User-Agent": "gnc-api-launcher"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())["sha"]
    except Exception:
        return None


def _local_sha():
    return SHA_FILE.read_text().strip() if SHA_FILE.exists() else None


def _download_update(sha):
    url = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/{GITHUB_BRANCH}.zip"
    print("  Descargando actualizacion desde GitHub...")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "update.zip"
            urllib.request.urlretrieve(url, zip_path)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmp)
            src_root = next(p for p in Path(tmp).iterdir() if p.is_dir())
            for item in src_root.iterdir():
                if item.name in SKIP_UPDATE:
                    continue
                dest = ROOT / item.name
                if item.is_dir():
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
        SHA_FILE.write_text(sha)
        return True
    except Exception as e:
        print(f"  Advertencia: no se pudo aplicar la actualizacion ({e})")
        return False


def auto_update():
    print("Verificando actualizaciones...")
    remote = _remote_sha()
    if remote is None:
        print("  Sin conexion, usando version local.")
        return False

    local = _local_sha()
    if local == remote:
        print("  Version al dia.")
        return False

    if local is None:
        print("  Primera verificacion de version...")
    else:
        print(f"  Nueva version disponible ({local[:8]} -> {remote[:8]})")

    updated = _download_update(remote)
    if updated:
        print("  Actualizacion aplicada. Reinstalando dependencias...")
        subprocess.run([str(VENV_PY), "-m", "pip", "install", "-r",
                        str(ROOT / "requirements.txt"), "-q"])
    return updated


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Prevent multiple launcher instances via a PID lock file
    lock_path = ROOT / "_launcher.lock"
    if lock_path.exists():
        try:
            old_pid = int(lock_path.read_text().strip())
            # Check if that PID is still alive
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, old_pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                print(f"  Ya hay un launcher corriendo (PID {old_pid}). Saliendo.")
                return
        except Exception:
            pass  # stale lock — proceed
    lock_path.write_text(str(os.getpid()))
    try:
        _main_body()
    finally:
        lock_path.unlink(missing_ok=True)


def _main_body():
    print("=" * 55)
    print("  GNC API")
    print("=" * 55)

    if not VENV_PY.exists():
        print("Buscando Python...")
        python_cmd = find_python()
        if not python_cmd:
            abort(
                "No se encontro Python 3 instalado.\n\n"
                " Para instalar Python:\n"
                "   1. Ve a: https://www.python.org/downloads/\n"
                "   2. Descarga Python 3.11 o superior\n"
                "   3. Durante la instalacion, marca 'Add Python to PATH'\n"
                "   4. Reinicia este launcher"
            )

        print("Creando entorno virtual...")
        r = subprocess.run(python_cmd + ["-m", "venv", str(ROOT / ".venv")])
        if r.returncode != 0:
            abort("No se pudo crear el entorno virtual.")

        remote = _remote_sha()
        if remote and remote != _local_sha():
            _download_update(remote)

        print()
        print("=" * 55)
        print("  Instalando dependencias (primera vez)...")
        print("  Esto puede tardar varios minutos.")
        print("=" * 55)
        subprocess.run([str(VENV_PY), "-m", "pip", "install", "--upgrade", "pip", "-q"])
        r = subprocess.run([str(VENV_PY), "-m", "pip", "install", "-r",
                            str(ROOT / "requirements.txt")])
        if r.returncode != 0:
            abort("No se pudieron instalar las dependencias.")
    else:
        auto_update()

    check = subprocess.run(
        [str(VENV_PY), "-c", "import fastapi, uvicorn"],
        capture_output=True,
    )
    if check.returncode != 0:
        print("Actualizando dependencias...")
        subprocess.run([str(VENV_PY), "-m", "pip", "install", "-r",
                        str(ROOT / "requirements.txt"), "-q"])

    print("Iniciando GNC API en http://localhost:50504 ...")
    kiosk_script = ROOT / "kiosk_server.py"
    if kiosk_script.exists():
        print("Iniciando Kiosk en http://localhost:50505 ...")
    print()

    _kiosk_running = threading.Event()
    _kiosk_running.set()

    def _kiosk_watchdog():
        """Mantiene kiosk_server.py vivo mientras _kiosk_running esté activo."""
        while _kiosk_running.is_set():
            proc = subprocess.Popen([str(VENV_PY), str(kiosk_script)])
            proc.wait()
            if _kiosk_running.is_set():
                time.sleep(2)  # pausa breve antes de reiniciar

    kiosk_thread = None
    if kiosk_script.exists():
        kiosk_thread = threading.Thread(target=_kiosk_watchdog, daemon=True)
        kiosk_thread.start()

    while True:
        r = subprocess.run([str(VENV_PY), str(ROOT / "main.py")])
        if r.returncode == 0:
            print("\n Servidor detenido correctamente.")
            break
        print(f"\n [!] Servidor cerro inesperadamente (codigo {r.returncode}).")
        print("     Reiniciando en 5 segundos... (Ctrl+C para cancelar)\n")
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("\n Reinicio cancelado.")
            break

    _kiosk_running.clear()


if __name__ == "__main__":
    main()
