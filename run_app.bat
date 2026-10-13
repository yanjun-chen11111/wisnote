@echo off
rem WisNote 一键启动：自动用项目 venv 运行 Streamlit 应用
cd /d "%~dp0"
.venv\Scripts\python.exe -m streamlit run ui/app.py
pause
