# blueprints/utils.py
from flask import Blueprint, redirect, url_for, flash
from models import Customer
from flask_login import login_required, current_user

utils_bp = Blueprint('utils', __name__)


@utils_bp.route('/clean_empty_customers')
def clean_empty_customers():
    deleted = Customer.delete().where(Customer.name == '').execute()
    flash(f'已删除 {deleted} 条空客户记录', 'success')
    return redirect(url_for('customers.manage_customers'))