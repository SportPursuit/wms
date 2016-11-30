
from openerp.osv import fields, orm


class delivery_warehouse_carrier(orm.Model):
    _name = "delivery.warehouse.carrier"

    _columns = {
        'name': fields.char('Name', size=128),
        'carrier_code': fields.char('Carrier Code', size=64, required=True),
        'tracking_link': fields.char('Tracking Link', size=128),
    }


delivery_warehouse_carrier()