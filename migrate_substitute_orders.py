"""
补建替代发货的客户订单记录
运行：python migrate_substitute_orders.py
"""
from models import db, CustomerOrder, CustomerOrderItem, SalesOrder, SalesOrderItem, User
from peewee import fn
from collections import defaultdict
import datetime

def run():
    user = User.get_or_none(User.username == 'admin')
    if not user:
        users = list(User.select().limit(1))
        if not users:
            print('No users found')
            return
        user = users[0]

    created = 0

    for co in CustomerOrder.select().where(CustomerOrder.user == user):
        co_product_ids = {it.product.id for it in co.items}
        if not co_product_ids:
            continue

        # 按产品汇总替代品发货
        subs = defaultdict(lambda: {'qty': 0, 'amount': 0, 'price': 0, 'so_items': []})
        for so in SalesOrder.select().where(
            (SalesOrder.customer_order == co) & (SalesOrder.user == user)
        ):
            for si in SalesOrderItem.select().where(SalesOrderItem.order == so):
                if si.product.id not in co_product_ids:
                    subs[si.product.id]['qty'] += si.quantity
                    subs[si.product.id]['amount'] += si.subtotal
                    subs[si.product.id]['price'] = si.unit_price
                    subs[si.product.id]['so_items'].append((so.id, si.id))

        if not subs:
            continue

        new_total = sum(v['amount'] for v in subs.values())
        print(f'Order #{co.id}: creating substitute order, total={new_total:.2f}')

        with db.atomic():
            # 1. 创建客户订单
            new_order = CustomerOrder.create(
                user=user,
                customer=co.customer,
                order_date=datetime.date.today(),
                total_amount=new_total,
                status='shipped',
                invoice_required=False,
                remark=f'替代发货记录（原订单 #{co.id}，历史数据补建）'
            )
            for pid, v in subs.items():
                CustomerOrderItem.create(
                    order=new_order,
                    product=pid,
                    quantity=v['qty'],
                    unit_price=v['price'],
                    subtotal=v['amount'],
                    user=user
                )
            print(f'  -> CustomerOrder #{new_order.id} created with {len(subs)} items')

            # 2. 创建出库单（关联到新订单）
            new_ship = SalesOrder.create(
                customer=co.customer,
                customer_order=new_order,
                order_date=datetime.date.today(),
                total_amount=new_total,
                remark=f'历史替代发货（原订单 #{co.id} 换货出库）',
                user=user
            )

            # 3. 把替代品的 SalesOrderItem 移到新出库单
            for pid, v in subs.items():
                for old_so_id, old_si_id in v['so_items']:
                    si = SalesOrderItem.get_by_id(old_si_id)
                    si.order = new_ship
                    si.save()

            # 4. 修正旧出库单总金额
            for old_so_id in {sid for v in subs.values() for sid, _ in v['so_items']}:
                old_so = SalesOrder.get_by_id(old_so_id)
                remaining = (SalesOrderItem
                            .select(fn.SUM(SalesOrderItem.subtotal))
                            .where(SalesOrderItem.order == old_so)
                            .scalar()) or 0
                if remaining > 0:
                    old_so.total_amount = remaining
                    old_so.save()
                else:
                    # 出库单已空，删除
                    SalesOrderItem.delete().where(SalesOrderItem.order == old_so).execute()
                    old_so.delete_instance()
                    print(f'  -> Deleted empty SalesOrder #{old_so_id}')

            created += 1

    print(f'\nDone: {created} substitute orders created')

if __name__ == '__main__':
    run()
