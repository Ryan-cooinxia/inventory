from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User
from extensions import limiter

auth_bp = Blueprint('auth', __name__)

def _safe_next_url(next_page):
    if next_page and next_page.startswith('/') and not next_page.startswith('//'):
        return next_page
    return url_for('home.index')

@auth_bp.route('/register', methods=['GET', 'POST'])
@limiter.limit("3 per hour")
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if not username or not password:
            flash('用户名和密码不能为空', 'danger')
            return redirect(url_for('auth.register'))
        if len(username) < 3:
            flash('用户名至少需要 3 个字符', 'danger')
            return redirect(url_for('auth.register'))
        if len(password) < 6:
            flash('密码至少需要 6 个字符', 'danger')
            return redirect(url_for('auth.register'))
        if User.select().where(User.username == username).exists():
            flash('用户名已存在', 'danger')
            return redirect(url_for('auth.register'))
        user = User.create(
            username=username,
            password_hash=generate_password_hash(password),
            is_admin=False
        )
        login_user(user)
        flash('注册成功，已自动登录', 'success')
        return redirect(url_for('home.index'))
    return render_template('register.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.get_or_none(User.username == username)
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            flash('登录成功', 'success')
            next_page = request.args.get('next')
            return redirect(_safe_next_url(next_page))
        else:
            flash('用户名或密码错误', 'danger')
    return render_template('login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('已退出登录', 'info')
    return redirect(url_for('auth.login'))
