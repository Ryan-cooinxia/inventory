# blueprints/logs.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import OperationLog

logs_bp = Blueprint('logs', __name__)

@logs_bp.route('/admin/logs')
@login_required
def view_logs():
    if not current_user.is_admin:
        flash('无权限访问', 'danger')
        return redirect(url_for('home.index'))

    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
    query = OperationLog.select().order_by(OperationLog.created_at.desc())
    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    logs = query.paginate(page, per_page)

    return render_template('operation_logs.html', logs=logs,
                           page=page, per_page=per_page, total_pages=total_pages)