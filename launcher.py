"""
仓库记账系统启动器
双击 启动.bat 运行 → 自动打开浏览器 → 关闭窗口即停止服务
"""
import subprocess
import sys
import os
import webbrowser
import time
import shutil

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 清理
subprocess.run(["taskkill", "/f", "/im", "python.exe"], capture_output=True)
subprocess.run(["taskkill", "/f", "/im", "pythonw.exe"], capture_output=True)
for root, dirs, files in os.walk(".", topdown=False):
    for d in dirs:
        if d == "__pycache__":
            shutil.rmtree(os.path.join(root, d), ignore_errors=True)

# 启动
python_exe = os.path.join(".venv", "Scripts", "python.exe")
proc = subprocess.Popen([python_exe, "app.py"])

# 等 Flask 就绪
for _ in range(30):
    time.sleep(0.5)
    try:
        import urllib.request
        urllib.request.urlopen("http://127.0.0.1:5000", timeout=1)
        break
    except:
        pass

webbrowser.open("http://127.0.0.1:5000")
print(f"\n{'='*40}")
print(f"  浏览器已打开 http://127.0.0.1:5000")
print(f"  关闭此窗口即停止服务")
print(f"{'='*40}\n")

try:
    proc.wait()
except KeyboardInterrupt:
    pass
finally:
    print("正在停止...")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    print("已停止。")
