@echo off
REM Windows: 重启 = 先停后起,复用现有脚本
call "%~dp0stop_mail_bridge_8880.bat" %*
call "%~dp0start_mail_bridge_8880.bat" %*
