from openerp.osv import osv, fields
import openerp.addons.decimal_precision as dp

from .supplier_stock import SUPPLIER_STOCK_FEED


class product_product(osv.osv):
    _inherit = "product.product"
    
    def _product_available_supplier(self, cr, uid, ids, field_names=None, arg=False, context=None):

        field_names = field_names or []

        if 'supplier_virtual_available_combined' not in field_names:
            return super(product_product, self)._product_available_supplier(
                cr, uid, ids, field_names=field_names, arg=arg, context=context
            )

        feed_enabled_products = []
        feed_disabled_products = []

        for product in self.browse(cr, uid, ids, context=context):
            if any(product.seller_ids.name.stock_feed_enabled):
                feed_enabled_products.append(product.id)
            else:
                feed_disabled_products.append(product.id)

        result = {}

        if feed_enabled_products:
            c = context.copy()
            c['location'] = SUPPLIER_STOCK_FEED
            products = self.get_product_available(cr, uid, feed_enabled_products, context=c)
            for product, qty in products.iteritems():
                result[product]['supplier_virtual_available_combined'] = qty

        else:
             result.update( super(product_product,self)._product_available_supplier(
                cr, uid, ids, field_names=field_names, arg=arg, context=context
            ))

        return result

    _columns = {
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
