@echo off
rem Video Studio CLI for Windows. Usage: vs --help
pushd "%~dp0"
python -m studio %*
set RC=%ERRORLEVEL%
popd
exit /b %RC%
