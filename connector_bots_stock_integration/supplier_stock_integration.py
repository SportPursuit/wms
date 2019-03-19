from openerp.osv import osv, fields

class supplier(osv.osv):
    _inherit = 'res.partner'
    
    _columns = {
        'stock_feed_enabled': fields.boolean(
            'Enable Stock Feed Integration',
            help="Use supplier quantity supplied via stock feed instead of normal backorder limit"
        ),
        'stock_feed_threshold': fields.integer(
            'Stock Feed Threshold',
            help="Set the stock quantity of products to 0 when importing stock quantities through the supplier feed and the value given is less or equal to the treshold"
        ),
        'flag_skus_out_of_stock': fields.boolean(
            'Clear stock if missing from supplier feed',
            help="Set products as out of stock if they are not included in the supplier integration feed"
        )
    }
