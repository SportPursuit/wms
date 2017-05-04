from openerp.osv import osv, fields
from openerp.tools.translate import _
import openerp.addons.decimal_precision as dp

class supplier(osv.osv):
    _inherit = 'res.partner'
    
    _columns = {
    'percent_to_exclude':fields.integer('Stock % to Exclude', help="stock % has to exclude for the Supplier while importing the Stock"),
    'flg_sku_out_of_stock':fields.boolean('Is Out of Stock', help="Barcode not found in sheet has to mark as Out of Stock or not")
    }
