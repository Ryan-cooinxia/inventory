# log_utils.py
from models import OperationLog

def log_action(user, action_type, target_type, target_id=None, description=None, ip_address=None):
    """记录操作日志"""
    if not user or not user.id:
        return
    OperationLog.create(
        user=user,
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        description=description,
        ip_address=ip_address
    )