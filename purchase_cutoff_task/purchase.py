# -*- coding: utf-8 -*-
# (c) 2016 credativ ltd. - Ondřej Kuzník
# License AGPL-3 - See http://www.gnu.org/licenses/agpl-3.0.html
from openerp.osv import orm

from openerp.addons.queue_tasks.queue_task import defer


class PurchaseOrder(orm.Model):
    _inherit = 'purchase.order'

    @defer("Cut-off Purchase Order")
    def purchase_cutoff_defer(self, cr, uid, ids, context=None):
        bots_warehouse = self.pool.get('bots.warehouse')
        for group in self.read_group(cr, uid, [('id', 'in', ids)], ['warehouse_id'], ['warehouse_id'], context=context):
            warehouse = bots_warehouse.browse(cr, uid, bots_warehouse.search(
                cr, uid, [('warehouse_id', '=', group['warehouse_id'][0])]
            ), context=context)
            purchase_ids = self.search(cr, uid, group['__domain'], context=context)
            # assuming only one warehouse for bots.warehouse
            warehouse = warehouse[0] if isinstance(warehouse, list) else warehouse
            warehouse.purchase_cutoff(purchase_ids)
