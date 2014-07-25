# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright 2014 credativ Ltd
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

import logging
from openerp.osv import orm, fields
from .unit.binder import BotsModelBinder
from .unit.backend_adapter import BotsCRUDAdapter
from .backend import bots
from .connector import get_environment
from openerp.addons.connector.queue.job import job
from openerp.addons.connector.event import on_record_create
from openerp.addons.connector_wms.event import on_picking_out_available, on_picking_in_available #, on_picking_out_cancel, on_picking_in_cancel
from openerp.addons.connector.exception import MappingError, InvalidDataError, JobError
from openerp.addons.connector.unit.synchronizer import (ImportSynchronizer,
                                                        ExportSynchronizer
                                                        )
from openerp import netsvc
from openerp import SUPERUSER_ID
from openerp.tools.translate import _
from openerp.tools.misc import DEFAULT_SERVER_DATETIME_FORMAT

from datetime import datetime
import json

_logger = logging.getLogger(__name__)

class StockPickingIn(orm.Model):
    _inherit = 'stock.picking.in'

    def bots_test_exported(self, cr, uid, ids, doraise=False, context=None):
        exported = self.pool.get('bots.stock.picking.in').search(cr, SUPERUSER_ID, [('openerp_id', 'in', ids)], context=context)
        if exported and doraise:
            raise osv.except_osv(_('Error!'), _('This picking has been exported to an external WMS and cannot be modified directly in OpenERP.'))
        return exported or False

    def cancel_assign(self, cr, uid, ids, context=None):
        res = super(StockPickingIn, self).cancel_assign(cr, uid, ids, context=context)
        self.bots_test_exported(cr, uid, ids, doraise=True, context=context)
        return res

    def action_cancel(self, cr, uid, ids, context=None):
        res = super(StockPickingIn, self).action_cancel(cr, uid, ids, context=context)
        self.bots_test_exported(cr, uid, ids, doraise=True, context=context)
        return res

    def action_done(self, cr, uid, ids, context=None):
        res = super(StockPickingIn, self).action_done(cr, uid, ids, context=context)
        self.bots_test_exported(cr, uid, ids, doraise=True, context=context)
        return res

    def unlink(self, cr, uid, ids, context=None):
        res = super(StockPickingIn, self).unlink(cr, uid, ids, context=context)
        self.bots_test_exported(cr, uid, ids, doraise=True, context=context)
        return res

class StockPickingOut(orm.Model):
    _inherit = 'stock.picking.out'

    def bots_test_exported(self, cr, uid, ids, doraise=False, context=None):
        exported = self.pool.get('bots.stock.picking.out').search(cr, SUPERUSER_ID, [('openerp_id', 'in', ids)], context=context)
        if exported and doraise:
            raise osv.except_osv(_('Error!'), _('This picking has been exported to an external WMS and cannot be modified directly in OpenERP.'))
        return exported or False

    def cancel_assign(self, cr, uid, ids, context=None):
        res = super(StockPickingOut, self).cancel_assign(cr, uid, ids, context=context)
        self.bots_test_exported(cr, uid, ids, doraise=True, context=context)
        return res

    def action_cancel(self, cr, uid, ids, context=None):
        res = super(StockPickingOut, self).action_cancel(cr, uid, ids, context=context)
        self.bots_test_exported(cr, uid, ids, doraise=True, context=context)
        return res

    def action_done(self, cr, uid, ids, context=None):
        res = super(StockPickingOut, self).action_done(cr, uid, ids, context=context)
        self.bots_test_exported(cr, uid, ids, doraise=True, context=context)
        return res

    def unlink(self, cr, uid, ids, context=None):
        res = super(StockPickingOut, self).unlink(cr, uid, ids, context=context)
        self.bots_test_exported(cr, uid, ids, doraise=True, context=context)
        return res

class StockMove(orm.Model):
    _inherit = 'stock.move'

    def bots_test_exported(self, cr, uid, ids, doraise=False, context=None):
        exported = False
        for move in self.browse(cr, uid, ids, context=context):
            if move.picking_id and move.picking_id.type == 'out':
                exported = self.pool.get('stock.picking.out').bots_test_exported(cr, uid, ids, doraise=doraise, context=context)
            elif move.picking_id and move.picking_id.type == 'in':
                exported = self.pool.get('stock.picking.in').bots_test_exported(cr, uid, ids, doraise=doraise, context=context)
            if exported:
                return exported
        return False

    def cancel_assign(self, cr, uid, ids, context=None):
        res = super(StockMove, self).cancel_assign(cr, uid, ids, context=context)
        self.bots_test_exported(cr, uid, ids, doraise=True, context=context)
        return res

    def action_cancel(self, cr, uid, ids, context=None):
        res = super(StockMove, self).action_cancel(cr, uid, ids, context=context)
        self.bots_test_exported(cr, uid, ids, doraise=True, context=context)
        return res

    def action_done(self, cr, uid, ids, context=None):
        res = super(StockMove, self).action_done(cr, uid, ids, context=context)
        self.bots_test_exported(cr, uid, ids, doraise=True, context=context)
        return res

    def action_scrap(self, cr, uid, ids, product_qty, location_id, context=None):
        res = super(StockMove, self).action_scrap(cr, uid, ids, product_qty, location_id, context=context)
        self.bots_test_exported(cr, uid, ids, doraise=True, context=context)
        return res

    def unlink(self, cr, uid, ids, context=None):
        res = super(StockMove, self).unlink(cr, uid, ids, context=context)
        self._test_exported(cr, uid, ids, doraise=True, context=context)
        return res

class BotsStockPickingOut(orm.Model):
    _name = 'bots.stock.picking.out'
    _inherit = 'bots.binding'
    _inherits = {'stock.picking.out': 'openerp_id'}
    _description = 'Bots Stock Picking Out'

    _columns = {
        'openerp_id': fields.many2one('stock.picking.out',
                                      string='Stock Picking Out',
                                      required=True,
                                      ondelete='restrict'),
        'warehouse_id': fields.many2one('bots.warehouse',
                                      string='Bots Warehouse',
                                      required=True,
                                      ondelete='restrict'),
        }

    _sql_constraints = [
        ('bots_picking_out_uniq', 'unique(backend_id, openerp_id)',
         'A Bots picking already exists for this picking for the same backend.'),
    ]

class BotsStockPickingIn(orm.Model):
    _name = 'bots.stock.picking.in'
    _inherit = 'bots.binding'
    _inherits = {'stock.picking.in': 'openerp_id'}
    _description = 'Bots Stock Picking In'

    _columns = {
        'openerp_id': fields.many2one('stock.picking.in',
                                      string='Stock Picking In',
                                      required=True,
                                      ondelete='restrict'),
        'warehouse_id': fields.many2one('bots.warehouse',
                                      string='Bots Warehouse',
                                      required=True,
                                      ondelete='restrict'),
        }

    _sql_constraints = [
        ('bots_picking_in_uniq', 'unique(backend_id, openerp_id)',
         'A Bots picking already exists for this picking for the same backend.'),
    ]

@bots
class BotsStockPickingOutBinder(BotsModelBinder):
    _model_name = [
            'bots.stock.picking.out',
        ]

@bots
class BotsStockPickingInBinder(BotsModelBinder):
    _model_name = [
            'bots.stock.picking.in',
        ]

class StockPickingAdapter(BotsCRUDAdapter):
    _picking_type = None

    def create(self, picking_id):

        if self._picking_type == 'in':
            MODEL = 'bots.stock.picking.in'
            TYPE = 'in'
            FILENAME = 'picking_in_%s.json'
        elif self._picking_type == 'out':
            MODEL = 'bots.stock.picking.out'
            TYPE = 'out'
            FILENAME = 'picking_out_%s.json'
        else:
            raise NotImplementedError('Unable to adapt stock picking of type %s' % (self._picking_type,))

        product_binder = self.get_binder_for_model('bots.product.product')
        picking_binder = self.get_binder_for_model(MODEL)
        bots_picking_obj = self.session.pool.get(MODEL)
        picking_obj = self.session.pool.get('stock.picking')
        move_obj = self.session.pool.get('stock.move')
        bots_warehouse_obj = self.session.pool.get('bots.warehouse')
        wf_service = netsvc.LocalService("workflow")

        picking = bots_picking_obj.browse(self.session.cr, self.session.uid, picking_id)
        default_company_id = picking.warehouse_id.warehouse_id.company_id.id
        if self.session.context and self.session.context.get('company_id'):
             default_company_id = self.session.context.get('company_id')
        ctx = (self.session.context or {}).copy()
        ctx.update({'company_id': default_company_id})
        default_company = self.session.pool.get('res.company').browse(self.session.cr, self.session.uid, default_company_id, context=ctx)

        picking = bots_picking_obj.browse(self.session.cr, self.session.uid, picking_id, context=ctx)
        if self._picking_type == 'out':
            order_number = picking.sale_id and picking.sale_id.name or picking.name
            address = picking.partner_id or picking.sale_id and picking.sale_id.partner_shipping_id
        elif self._picking_type == 'in':
            order_number = picking.purchase_id and picking.purchase_id.name or picking.name
            address = picking.partner_id or picking.purchase_id and (picking.purchase_id.warehouse_id and picking.purchase_id.warehouse_id.partner_id or picking.purchase_id.dest_address_id)
        else:
            order_number = picking.name
            address = picking.partner_id

        if picking.bots_id:
            raise JobError(_('The Bots picking %s already has an external ID. Will not export again.') % (picking.id,))

        if not address:
            raise MappingError(_('Missing address when attempting to export Bots picking %s.') % (picking_id,))

        # Get a unique name for the picking
        bots_id = '%s' % (order_number,)
        # Test if this ID is unique, if not increment it
        suffix_counter = 0
        existing_id = picking_binder.to_openerp(bots_id)
        orig_bots_id = bots_id
        while existing_id:
            suffix_counter += 1
            bots_id = "%sS%s" % (orig_bots_id, suffix_counter)
            existing_id = picking_binder.to_openerp(bots_id)

        # Select which moves we will ship
        picking_complete = True
        moves_to_split = []
        order_lines = []
        seq = 1
        for move in picking.move_lines:
            if move.state != 'assigned':
                picking_complete = False
                moves_to_split.append(move.id)
                continue
            product_bots_id = move.product_id and product_binder.to_backend(move.product_id.id)
            if not product_bots_id:
                picking_complete = False
                moves_to_split.append(move.id)
                continue
            order_line = {
                    "id": "%s-%s" % (bots_id, seq),
                    "seq": seq,
                    "product": product_bots_id, 
                    "product_qty": int(move.product_qty),
                    "uom": move.product_uom.name,
                    "product_uos_qty": int(move.product_uos_qty),
                    "uos": move.product_uos.name,
                    "price_unit": move.price_unit \
                        or move.sale_line_id and move.sale_line_id.price_unit \
                        or move.purchase_line_id and move.purchase_line_id.price_unit \
                        or move.product_id.standard_price,
                    "price_currency": move.price_unit and move.price_currency_id.name \
                        or move.sale_line_id.price_unit and move.sale_line_id.company_id.currency_id.name \
                        or move.purchase_line_id.price_unit and move.purchase_line_id.company_id.currency_id.name \
                        or default_company.currency_id.name,
                }
            if move.product_id.volume:
                order_line['volume_net'] = move.product_id.volume
            if move.product_id.weight:
                order_line['weight'] = move.product_id.weight
            if move.product_id.weight_net:
                order_line['weight_net'] = move.product_id.weight_net
            if move.note:
                order_line['desc'] = move.note

            order_lines.append(order_line)

        if not order_lines:
            raise MappingError(_('Unable to export any order lines on export of Bots picking %s.') % (picking_id,))

        # Split picking depending on order policy
        if not picking_complete:
            picking_policy = picking.sale_id and picking.sale_id.picking_policy or 'direct'
            if picking_policy != 'direct':
                raise InvalidDataError(_('Unable to export picking %s. Picking policy does not allow it to be split and is not fully complete or some products are not mapped for export.') % (picking_id,))
            # Split the picking
            new_picking_id = picking_obj.copy(cr, uid, picking.openerp_id.id, context=ctx)
            move_obj.write(cr, uid, moves_to_split, {'picking_id': new_picking_id}, context=ctx)
            wf_service.trg_validate(self.session.uid, 'stock.picking', new_picking_id, 'button_confirm', self.session.cr)

        picking_data = {
                'id': bots_id,
                'name': bots_id,
                'order': bots_id,
                'state': 'new',
                'type': TYPE,
                'date': datetime.strptime(picking.min_date, DEFAULT_SERVER_DATETIME_FORMAT).strftime('%Y-%m-%d'),
                'partner':
                    {
                        "id": "P%d" % (picking.partner_id.id),
                        "name": picking.partner_id.name,
                        "street1": picking.partner_id.street,
                        "street2": picking.partner_id.street2,
                        "city": picking.partner_id.city,
                        "zip": picking.partner_id.zip,
                        "country": picking.partner_id.country_id and picking.partner_id.country_id.code or '',
                        "state": picking.partner_id.state_id and picking.partner_id.state_id.name or '',
                        "phone": picking.partner_id.phone,
                        "fax": picking.partner_id.fax,
                        "email": picking.partner_id.email,
                        "language": picking.partner_id.lang,
                    },
                'line': order_lines,
            }
        if picking.note:
            picking_data['desc'] = picking.note
        if picking.partner_id.vat:
            picking_data['partner']['vat'] = picking.partner_id.vat

        data = {
                'picking': {
                        'pickings': [picking_data,]
                        'header': [{
                                'type': TYPE,
                                'state': 'done',
                                'partner_to': picking.backend_id.name_to,
                                'partner_from': picking.backend_id.name_from,
                                'message_id': '0',
                                'date_msg': datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f'),
                            }],
                    },
                }
        data = json.dumps(data, indent=4)

        filename_id = self._get_unique_filename(FILENAME)
        res = self._write(filename_id, data)
        return bots_id

@bots
class StockPickingOutAdapter(StockPickingAdapter):
    _model_name = 'bots.stock.picking.out'
    _picking_type = 'out'

@bots
class StockPickingInAdapter(StockPickingAdapter):
    _model_name = 'bot.stock.picking.in'
    _picking_type = 'in'

def picking_available(session, model_name, record_id, picking_type, location_type):
    warehouse_obj = session.pool.get('stock.warehouse')
    bots_warehouse_obj = session.pool.get('bots.warehouse')
    picking = session.browse(model_name, record_id)
    # Check to see if the picking should be exported to the WMS
    # If so create binding, else return
    if not picking.state == 'assigned': # Handle only deliveries which are assigned
        return
    if location_type == 'src':
        location_id = picking.location_id.id or picking.move_lines and picking.move_lines[0].location_id.id
    else:
        location_id = picking.location_dest_id.id or picking.move_lines and picking.move_lines[0].location_dest_id.id
    warehouse_ids = warehouse_obj.search(session.cr, session.uid, [('lot_stock_id', '=', location_id)])
    bots_warehouse_ids = bots_warehouse_obj.search(session.cr, session.uid, [('warehouse_id', 'in', warehouse_ids)])
    bots_warehouse = bots_warehouse_obj.browse(session.cr, session.uid, bots_warehouse_ids)
    for warehouse in bots_warehouse:
        backend_id = warehouse.backend_id
        if (picking_type == 'bots.stock.picking.out' and backend_id.feat_picking_out) or \
            (picking_type == 'bots.stock.picking.in' and backend_id.feat_picking_in):
            session.create(picking_type,
                            {'backend_id': backend_id.id,
                            'openerp_id': picking.id,
                            'warehouse_id': warehouse['id'],})

@bots
class BotsPickingExport(ExportSynchronizer):
    _model_name = ['bots.stock.picking.in',
                   'bots.stock.picking.out']

    def run(self, binding_id):
        """
        Export the picking to Bots
        """
        bots_id = self.backend_adapter.create(binding_id)
        self.binder.bind(bots_id, binding_id)

@on_record_create(model_names='bots.stock.picking.out')
def delay_export_picking_out_available(session, model_name, record_id, vals):
    export_picking_available.delay(session, model_name, record_id)

@on_record_create(model_names='bots.stock.picking.in')
def delay_export_picking_in_available(session, model_name, record_id, vals):
    export_picking_available.delay(session, model_name, record_id)

@job
def export_picking_available(session, model_name, record_id):
    picking = session.browse(model_name, record_id)
    backend_id = picking.backend_id.id
    env = get_environment(session, model_name, backend_id)
    picking_exporter = env.get_connector_unit(BotsPickingExport)
    res = picking_exporter.run(record_id)
    return res

#def picking_cancel(session, model_name, record_id, picking_type):
#    warehouse_obj = session.pool.get('stock.warehouse')
#    bots_warehouse_obj = session.pool.get('bots.warehouse')
#    picking = session.browse(model_name, record_id)
#    # TODO: Check if we are already exported, if so export a 'cancel'
#    raise NotImplementedError("NIE")
#    # Check to see if the picking should be exported to the WMS
#    # If so create binding, else return
#    if not picking.state == 'assigned': # Handle only deliveries which are assigned
#        return
#    location_id = picking.location_id.id or picking.move_lines and picking.move_lines[0].location_id.id
#    warehouse_ids = warehouse_obj.search(session.cr, session.uid, [('lot_stock_id', '=', location_id)])
#    bots_warehouse_ids = bots_warehouse_obj.search(session.cr, session.uid, [('warehouse_id', 'in', warehouse_ids)])
#    bots_warehouse = bots_warehouse_obj.read(session.cr, session.uid, bots_warehouse_ids, ['backend_id'])
#    for warehouse in bots_warehouse:
#        backend_id = warehouse['backend_id'][0]
#        session.create(picking_type,
#                        {'backend_id': backend_id,
#                        'openerp_id': picking.id,
#                        'warehouse_id': warehouse['id'],})

@on_picking_out_available
def picking_out_available(session, model_name, record_id):
    return picking_available(session, model_name, record_id, 'bots.stock.picking.out', location_type='src')

@on_picking_in_available
def picking_in_available(session, model_name, record_id):
    return picking_available(session, model_name, record_id, 'bots.stock.picking.in', location_type='dest')

#@on_picking_out_cancel
#def picking_out_cancel(session, model_name, record_id):
#    return picking_cancel(session, model_name, record_id, 'bots.stock.picking.out'):

#@on_picking_in_cancel
#def picking_in_cancel(session, model_name, record_id):
#    return picking_cancel(session, model_name, record_id, 'bots.stock.picking.in'):
