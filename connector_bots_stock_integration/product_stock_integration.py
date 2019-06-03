import logging

from openerp.osv import osv, fields
import openerp.addons.decimal_precision as dp

logger = logging.getLogger(__name__)


class product_product(osv.osv):
    _inherit = "product.product"

    def _product_available_supplier_feed(self, cr, uid, ids, field_names=None, arg=False, context=None):
        products = {}.fromkeys(ids, 0.0)
        warehouse = context.get('warehouse', False)
        for product in self.browse(cr, uid, ids, context):
            default_warehouse = product.seller_id.default_warehouse_id
            # if warehouse is provided in the context and it is
            # different from the default_warehouse_id of the
            # main_supplier skip
            if warehouse and warehouse != default_warehouse.id:
                continue

            products.update(
                product.get_product_available(
                    context={'location': default_warehouse.lot_supplier_feed_id.id,
                             'states': ('confirmed', 'waiting', 'assigned', 'done'),
                             'what': ('in', 'out')}
                )
            )
        return products

    def _product_available_supplier(self, cr, uid, ids, field_names=None, arg=False, context=None):

        field_names = field_names or []

        if 'supplier_virtual_available_combined' not in field_names:
            return super(product_product, self)._product_available_supplier(
                cr, uid, ids, field_names=field_names, arg=arg, context=context
            )

        feed_enabled_products = []
        feed_disabled_products = []

        for product in self.browse(cr, uid, ids, context=context):
            feed_enabled = [supplier.name.stock_feed_enabled for supplier in product.seller_ids]

            if any(feed_enabled):
                feed_enabled_products.append(product.id)
            else:
                feed_disabled_products.append(product.id)

        result = super(product_product, self)._product_available_supplier(
            cr, uid, ids, field_names=field_names, arg=arg, context=context
        )

        if feed_enabled_products:
            products = self._product_available_supplier_feed(cr, uid, feed_enabled_products, context=context)

            for product, qty in products.iteritems():
                result[product]['supplier_virtual_available_combined'] += qty

        return result

    _columns = {
        'supplier_feed_quantity': fields.function(
            _product_available_supplier_feed,
            type='float', digits_compute=dp.get_precision('Product Unit of Measure'),
            string='Supplier Feed Quantity'
        ),
        'supplier_virtual_available_combined': fields.function(
            _product_available_supplier, multi='supplier_virtual_available',
            type='float', digits_compute=dp.get_precision('Product Unit of Measure'),
            string='Available Quantity inc. Supplier',
            help="Forecast quantity (computed as Quantity On Hand "
                 "- Outgoing + Incoming) at the virtual supplier location "
                 "for the current warehouse if applicable, otherwise 0, plus "
                 "the normal forecast quantity OR quantity provided by the supplier stock feed."
        ),
    }


class missing_products(osv.Model):
    _name = "supplier.feed.missing.products"

    _columns = {
        'filename': fields.char('CSV', size=60, readonly=True),
        'inventory_id': fields.many2one('stock.inventory', 'Physical Inventory', required=True),
        'supplier_id': fields.many2one('res.partner', 'Physical Inventory', required=True),
        'product_sku': fields.char('CSV', size=60, readonly=True),
        'product_barcode': fields.char('CSV', size=20, readonly=True),
        'quantity': fields.integer('Quantity', readonly=True)
    }
