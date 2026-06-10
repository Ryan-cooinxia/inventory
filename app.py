"""
仓库记账系统主程序
基于 Flask + Peewee + SQLite
包含产品/客户/供应商管理、入库/出库录单、统计报表
"""
import os
import sys
from flask import Flask
from flask_login import LoginManager           # 新增
from models import (
    db, Product, Customer, Supplier,
    ProductBundle, ProductBundleItem,
    PurchaseOrder, PurchaseOrderItem,
    SalesOrder, SalesOrderItem,
    CustomerOrder, CustomerOrderItem,
    SupplierOrder, SupplierOrderItem,
    CustomerRefund, CustomerTransaction,
    ExchangeRate, OperationLog,
    UserApiKey,        # 新增
    User
)

# 导入扩展
from extensions import limiter

# 导入蓝图
from blueprints.home import home_bp
from blueprints.products import products_bp
from blueprints.customers import customers_bp
from blueprints.suppliers import suppliers_bp
from blueprints.purchases import purchases_bp
from blueprints.sales import sales_bp
from blueprints.orders import orders_bp
from blueprints.supplier_orders import supplier_orders_bp
from blueprints.reports import reports_bp
from blueprints.refunds import refunds_bp
from blueprints.finance import finance_bp
from blueprints.data_io import data_io_bp
from blueprints.utils import utils_bp
from blueprints.exchange import exchange_bp
from blueprints.tools import tools_bp
from blueprints.auth import auth_bp
from blueprints.logs import logs_bp             # 新增
from blueprints.ai_import import ai_bp
from blueprints.agent import agent_bp

app = Flask(__name__)
app.secret_key = (
    os.environ.get('FLASK_SECRET_KEY')
    or os.environ.get('SECRET_KEY')
    or 'dev-secret-key-change-me'
)

# 初始化 Flask 扩展
limiter.init_app(app)

# ---------- Flask-Login 初始化 ----------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'        # 未登录时重定向到登录页
login_manager.login_message = '请先登录再访问此页面。'

@login_manager.user_loader
def load_user(user_id):
    """从 session 中恢复用户对象"""
    from models import User
    try:
        return User.get_or_none(User.id == int(user_id))
    except (TypeError, ValueError):
        return None
# ----------------------------------------

# 注册蓝图
app.register_blueprint(home_bp)
app.register_blueprint(products_bp)
app.register_blueprint(customers_bp)
app.register_blueprint(suppliers_bp)
app.register_blueprint(purchases_bp)
app.register_blueprint(sales_bp)
app.register_blueprint(orders_bp)
app.register_blueprint(supplier_orders_bp)
app.register_blueprint(reports_bp)
app.register_blueprint(refunds_bp)
app.register_blueprint(finance_bp)
app.register_blueprint(data_io_bp)
app.register_blueprint(utils_bp)
app.register_blueprint(exchange_bp)
app.register_blueprint(tools_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(logs_bp)
app.register_blueprint(ai_bp)  
app.register_blueprint(agent_bp)           # 新增

# 数据库连接管理
@app.before_request
def before_request():
    db.connect(reuse_if_open=True)

@app.after_request
def after_request(response):
    if not db.is_closed():
        db.close()
    return response

def init_db():
    db.create_tables([Product, Customer, Supplier,
                      ProductBundle, ProductBundleItem,
                      PurchaseOrder, PurchaseOrderItem,
                      SalesOrder, SalesOrderItem,
                      CustomerOrder, CustomerOrderItem,
                      SupplierOrder, SupplierOrderItem,
                      CustomerRefund, CustomerTransaction,
                      ExchangeRate, User,
                      OperationLog, UserApiKey], safe=True)       # 新增 OperationLog 表
    migrate_product_bundle_schema()

def migrate_product_bundle_schema():
    """兼容旧版套装组成表：bundle_product_id -> 独立 ProductBundle。"""
    columns = [row[1] for row in db.execute_sql('PRAGMA table_info(productbundleitem)').fetchall()]
    if not columns or 'bundle_id' in columns or 'bundle_product_id' not in columns:
        return

    db.execute_sql('ALTER TABLE productbundleitem RENAME TO productbundleitem_old')
    db.execute_sql(
        '''
        CREATE TABLE IF NOT EXISTS productbundleitem (
            id INTEGER NOT NULL PRIMARY KEY,
            bundle_id INTEGER NOT NULL,
            component_product_id INTEGER NOT NULL,
            quantity REAL NOT NULL,
            user_id INTEGER,
            FOREIGN KEY (bundle_id) REFERENCES productbundle (id),
            FOREIGN KEY (component_product_id) REFERENCES product (id),
            FOREIGN KEY (user_id) REFERENCES user (id)
        )
        '''
    )

    old_rows = db.execute_sql(
        '''
        SELECT old.component_product_id, old.quantity, old.user_id, product.name
        FROM productbundleitem_old AS old
        JOIN product ON product.id = old.bundle_product_id
        '''
    ).fetchall()
    with db.atomic():
        for component_product_id, quantity, user_id, bundle_name in old_rows:
            bundle, _ = ProductBundle.get_or_create(
                user=user_id,
                name=bundle_name
            )
            ProductBundleItem.get_or_create(
                bundle=bundle,
                component_product=component_product_id,
                defaults={'quantity': quantity, 'user': user_id}
            )
        db.execute_sql('DROP TABLE productbundleitem_old')
        db.execute_sql(
            '''
            CREATE UNIQUE INDEX IF NOT EXISTS productbundleitem_bundle_id_component_product_id
            ON productbundleitem (bundle_id, component_product_id)
            '''
        )


# 启动后台汇率更新（每小时一次，不阻塞请求）
def _start_services():
    from services import start_background_updater
    start_background_updater()

_start_services()

# 打包成 exe 后，模板路径修正
if getattr(sys, 'frozen', False):
    template_folder = os.path.join(sys._MEIPASS, 'templates')
    static_folder = os.path.join(sys._MEIPASS, 'static')
    app.template_folder = template_folder
    app.static_folder = static_folder

if __name__ == '__main__':
    with app.app_context():
        init_db()          # 自动建表
    debug = os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes', 'on')
    host = os.environ.get('FLASK_HOST', '127.0.0.1')
    port = int(os.environ.get('FLASK_PORT', '5000'))
    app.run(debug=debug, host=host, port=port)
