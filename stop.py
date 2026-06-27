"""强制关闭所有 Python 进程"""
import subprocess, sys, os

# 方法1: taskkill
subprocess.run(["taskkill", "/f", "/im", "python.exe"], capture_output=True)
subprocess.run(["taskkill", "/f", "/im", "pythonw.exe"], capture_output=True)

# 方法2: 按端口杀
result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
for line in result.stdout.split('\n'):
    if ':5000' in line and 'LISTENING' in line:
        parts = line.strip().split()
        pid = parts[-1]
        subprocess.run(["taskkill", "/f", "/pid", pid], capture_output=True)
        print(f"Killed PID {pid} on port 5000")

# 方法3: wmic 杀所有 python
subprocess.run(
    'wmic process where "name like \'%python%\'" call terminate',
    shell=True, capture_output=True
)

print("Done. All Python processes stopped.")
os.system("pause")
