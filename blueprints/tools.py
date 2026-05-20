# blueprints/tools.py
from flask import Blueprint, render_template
from flask_login import login_required, current_user

tools_bp = Blueprint('tools', __name__)

@tools_bp.route('/tools/pricing')
def pricing_tool():
    return render_template('tools_pricing.html')