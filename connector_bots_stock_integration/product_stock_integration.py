from openerp.osv import osv, fields
import openerp.addons.decimal_precision as dp

    
class product(osv.osv):
    _inherit = "product.product"
    
    def _product_available_supplier(self, cr, uid, ids, field_names=None, arg=False, context=None):
        res = super(product,self)._product_available_supplier(cr, uid, ids, field_names=field_names, arg=arg, context=context)
        integration_qty_data = self.read(cr, uid, ids, ['supplier_stock_integration_qty'])
        integration_qty = {v['id']: v['supplier_stock_integration_qty'] for v in integration_qty_data}
        for id in ids:
            res[id]['supplier_virtual_available_combined'] = res[id]['supplier_virtual_available_combined'] + integration_qty.get(id, 0.0)
        return res
    
    _columns = {
        'supplier_stock_integration_qty': fields.float(
            string='Supplier Integration Quantity', digits_compute=dp.get_precision('Product Unit of Measure')
        ),
        'supplier_virtual_available_combined': fields.function(
            _product_available_supplier, multi='supplier_virtual_available',
            type='float',  digits_compute=dp.get_precision('Product Unit of Measure'),
            string='Available Quantity inc. Supplier',
            help="Forecast quantity (computed as Quantity On Hand "
                 "- Outgoing + Incoming) at the virtual supplier location "
                 "for the current warehouse if applicable, otherwise 0, plus "
                 "the normal forecast quantity and quantity provided by the supplier feed."
        ),
    }
