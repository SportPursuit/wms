from openerp.osv import osv, fields

class supplier(osv.osv):
    _inherit = 'res.partner'
    
    _columns = {
        'stock_feed_enabled': fields.boolean(
            'Enable Stock Feed Integration',
            help="Use supplier quantity supplied via stock feed instead of normal backorder limit"
        ),
        'percent_to_exclude': fields.integer(
            'Stock % to Exclude',
            help="Percentage of stock to be excluded from the supplier available quantity when importing stock quantities through the supplier feed"
        ),
        'flag_skus_out_of_stock': fields.boolean(
            'Clear stock if missing from supplier feed',
            help="Set products as out of stock if they are not included in the supplier integration feed"
        )
    }
