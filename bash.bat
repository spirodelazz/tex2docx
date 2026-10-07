@echo off
REM tex2docx: one-command LaTeX -> Word conversion.
REM Usage: bash.bat manuscript\tex\manuscript.tex -o output.docx
python "%~dp0convert.py" %*
