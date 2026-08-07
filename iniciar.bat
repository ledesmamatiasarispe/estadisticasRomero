@echo off
cd /d %~dp0
echo  Iniciando GNC API (puerto 50504) + Kiosk (puerto 50505)...
.venv\Scripts\python.exe launcher.py
pause
