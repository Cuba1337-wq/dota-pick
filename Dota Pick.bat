@echo off
rem Dota Pick: opens its own window, no console. Stops by itself when the window is closed.
cd /d "%~dp0"
start "" pythonw app.py
