
from openerp.osv import fields, orm


class delivery_warehouse_carrier(orm.Model):
    _name = 'delivery.warehouse.carrier'

    _columns = {
        'name': fields.char('Name', size=128, required=True),
        'carrier_code': fields.char('Carrier Code', size=64, required=True),
        'tracking_link': fields.char('Tracking Link', size=128),
    }


    def write(self, cr, user, ids, vals, context=None):
        tracking_link = vals.get('tracking_link')

        if tracking_link is not None and '[[code]]' not in tracking_link:
            raise orm.except_orm(
                'Error',
                'Invalid tracking link. It must contain [[code]]'
            )

        return super(delivery_warehouse_carrier, self).write(cr, user, ids, vals, context=context)


    def create(self, cr, user, vals, context=None):
        tracking_link = vals.get('tracking_link')

        if tracking_link is not None and '[[code]]' not in tracking_link:
            raise orm.except_orm(
                'Error',
                'Invalid tracking link. It must contain [[code]]'
            )

        return super(delivery_warehouse_carrier, self).create(cr, user, vals, context=context)


delivery_warehouse_carrier()