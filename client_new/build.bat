@echo off
echo 正在打包客户端...
pyinstaller --onefile --console --name=WebClusterClient --distpath=dist --workpath=build --specpath=. --add-data "config.json;." client.py
echo 打包完成！可执行文件在 dist 目录中
pause









