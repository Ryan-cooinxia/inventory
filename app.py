"""
仓库记账系统主程序
基于 Flask + Peewee + SQLite
包含产品/客户/供应商管理、入库/出库录单、统计报表
"""
import os
import sys
from flask import Flask
from models import (
    db, Product, Customer, Supplier,
    PurchaseOrder, PurchaseOrderItem,
    SalesOrder, SalesOrderItem,
    CustomerOrder, CustomerOrderItem,
    SupplierOrder, SupplierOrderItem,
    CustomerRefund, CustomerTransaction
)

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
from blueprints.tools import tools_bp

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-in-production'

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
app.register_blueprint(tools_bp)

# 数据库连接管理
@app.before_request
def before_request():
    db.connect()

@app.after_request
def after_request(response):
    db.close()
    return response

def init_db():
    db.create_tables([Product, Customer, Supplier,
                      PurchaseOrder, PurchaseOrderItem,
                      SalesOrder, SalesOrderItem,
                      CustomerOrder, CustomerOrderItem,
                      SupplierOrder, SupplierOrderItem,
                      CustomerRefund, CustomerTransaction], safe=True)

# 打包成 exe 后，模板路径修正
if getattr(sys, 'frozen', False):
    template_folder = os.path.join(sys._MEIPASS, 'templates')
    static_folder = os.path.join(sys._MEIPASS, 'static')
    app.template_folder = template_folder
    app.static_folder = static_folder

if __name__ == '__main__':
    with app.app_context():
        init_db()          # 自动建表
    app.run(debug=True, host='0.0.0.0', port=5000)