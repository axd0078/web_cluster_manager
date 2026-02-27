@echo off
echo 正在打包服务端...
pyinstaller --onefile --windowed --name=WebClusterServer --distpath=dist --workpath=build --specpath=. --add-data "config.json;." server.py
echo 打包完成！可执行文件在 dist 目录中
pause









