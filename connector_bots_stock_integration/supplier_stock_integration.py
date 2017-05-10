from openerp.osv import osv, fields

class supplier(osv.osv):
    _inherit = 'res.partner'
    
    _columns = {
        'percent_to_exclude': fields.integer(
            'Stock % to Exclude',
            help="Percentage of stock to be excluded from the supplier available quantity when importing stock quantities through the supplier feed"
        ),
        'flag_skus_out_of_stock': fields.boolean(
            'Is Out of Stock',
            help="Flag supplier products as out of stock if they are not included in the supplier integration feed"
        )
    }
