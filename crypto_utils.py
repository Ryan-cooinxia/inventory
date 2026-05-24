import os
from cryptography.fernet import Fernet

# 获取或生成加密密钥
SECRET_KEY = os.environ.get('ENCRYPTION_KEY')
if not SECRET_KEY:
    # 尝试从文件读取
    key_file = os.path.join(os.path.dirname(__file__), '.encryption_key')
    if os.path.exists(key_file):
        with open(key_file, 'r') as f:
            SECRET_KEY = f.read().strip()
    else:
        # 生成新密钥并保存
        SECRET_KEY = Fernet.generate_key().decode()
        with open(key_file, 'w') as f:
            f.write(SECRET_KEY)
        print(f"已生成加密密钥，保存在 {key_file}")
        print("请将该文件添加到 .gitignore，切勿提交到版本控制！")

cipher = Fernet(SECRET_KEY.encode() if isinstance(SECRET_KEY, str) else SECRET_KEY)

def encrypt_api_key(plain_text):
    return cipher.encrypt(plain_text.encode()).decode()

def decrypt_api_key(cipher_text):
    return cipher.decrypt(cipher_text.encode()).decode()