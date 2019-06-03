import logging

from openerp.osv import osv, fields
import openerp.addons.decimal_precision as dp


logger = logging.getLogger(__name__)


class product_product(osv.osv):
    _inherit = "product.product"

    def _get_available_supplier_feed_qty(self, cr, uid, ids, warehouse, context=None):
        context = context or {}
        products = {}.fromkeys(ids, 0.0)
        if not warehouse:
            return products

        product_ids = ', '.join([str(product_id) for product_id in ids])
        cr.execute("""
                        SELECT si.product_id
                        FROM product_supplierinfo AS si 
                        JOIN res_partner AS rp 
                            ON si."name" = rp.id
                        WHERE rp.default_warehouse_id = %s
                            AND si.product_id in (%s);
                        """, (warehouse.id, product_ids))

        product_default_warehouse_mapped = [product[0] for product in cr.fetchall()]
        logger.info("Pre update products: %s" % products)
        if product_default_warehouse_mapped:
            # WARNING: enforcing the warehouse to be False since
            # get_product_available overrides the location context
            # with the warehouse location lot_stock_id
            context['warehouse'] = False
            context['what'] = ('in', 'out')
            context['states'] = ('confirmed', 'waiting', 'assigned', 'done')
            context['location'] = warehouse.lot_supplier_feed_id.id
            logger.info("Context products: %s" % context)
            products.update(
                self.get_product_available(
                    cr, uid, product_default_warehouse_mapped, context=context
                )
            )

        logger.info("Post update products: %s" % products)
        return products

    def _product_available_supplier_feed(self, cr, uid, ids, field_names=None, arg=False, context=None):
        supplier = context.get('default_supplier', False)
        partner_obj = self.pool.get('res.partner')

        supplier = partner_obj.browse(cr, uid, supplier, context=context)
        warehouse = supplier and supplier.default_warehouse_id or False
        return self._get_available_supplier_feed_qty(cr, uid, ids, warehouse)

    # TODO: find a proper name for this
    def _product_available_supplier_feed_logic(self, cr, uid, ids, context=None):
        warehouse = context.get('warehouse', False)
        warehouse_obj = self.pool.get('stock.warehouse')

        if isinstance(warehouse, (str, unicode)):
            warehouse = warehouse_obj.browse(
                cr, uid, warehouse_obj.search(
                    cr, uid, [('name' '=', warehouse)]
                ), context=context
            )
        else:
            warehouse = warehouse_obj.browse(
                cr, uid, warehouse, context=context
            )

        return self._get_available_supplier_feed_qty(cr, uid, ids, warehouse)

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
            products = self._product_available_supplier_feed_logic(cr, uid, feed_enabled_products, context=context)

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
