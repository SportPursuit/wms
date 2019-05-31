import logging

from openerp.osv import osv, fields
import openerp.addons.decimal_precision as dp


logger = logging.getLogger(__name__)


class product_product(osv.osv):
    _inherit = "product.product"

    def _product_available_supplier_feed(self, cr, uid, ids, field_names=None, arg=False, context=None):
        warehouse_id = context.get('warehouse', False)

        c = context.copy()
        c['states'] = ('confirmed', 'waiting', 'assigned', 'done')
        c['what'] = ('in', 'out')
        # WARNING: enforcing the warehouse to be False since
        # get_product_available overrides the location context
        # with the warehouse location lot_stock_id
        c['warehouse'] = False

        products = {}.fromkeys(ids, 0.0)
        for product_id in ids:
            main_supplier = self._get_main_product_supplier(cr, uid, product_id, context)
            if main_supplier:
                if main_supplier.default_warehouse_id.id == warehouse_id:
                    c['location'] = main_supplier.default_warehouse_id.lot_supplier_feed_id
                    products.update(self.get_product_available(cr, uid, product_id, context=c))

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

        result = super(product_product,self)._product_available_supplier(
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
            type='float',  digits_compute=dp.get_precision('Product Unit of Measure'),
            string='Supplier Feed Quantity'
        ),
        'supplier_virtual_available_combined': fields.function(
            _product_available_supplier, multi='supplier_virtual_available',
            type='float',  digits_compute=dp.get_precision('Product Unit of Measure'),
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
