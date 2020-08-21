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

from openerp.osv import orm, fields, osv
from openerp import pooler, netsvc, SUPERUSER_ID
from openerp.tools.translate import _
from openerp.tools.misc import DEFAULT_SERVER_DATETIME_FORMAT

from openerp.addons.connector.session import ConnectorSession
from openerp.addons.connector.queue.job import job
from openerp.addons.connector.exception import JobError, NoExternalId
from openerp.addons.connector.unit.synchronizer import ImportSynchronizer
from openerp.addons.magentoerpconnect.stock_tracking import export_tracking_number

from .unit.binder import BotsModelBinder
from .unit.backend_adapter import BotsCRUDAdapter, file_to_process
from .backend import bots
from .connector import get_environment, add_checkpoint

import json
import traceback
from datetime import datetime

from psycopg2 import OperationalError

logger = logging.getLogger(__name__)

FILE_LOCK_MSG = 'could not obtain lock on row in relation "bots_file"'

NOT_TRACKED = 'NOT_TRACKED'
BLANK_LABEL = 'BL'


class BotsWarehouse(orm.Model):
    _name = 'bots.warehouse'
    _inherit = 'bots.binding'
    _description = 'Bots Warehouse Mapping'

    _columns = {
        'name': fields.char('Name', required=True),
        'warehouse_id': fields.many2one('stock.warehouse', 'Warehouse', required=True),
    }

    _sql_constraints = [
        ('bots_warehouse_uniq', 'unique(backend_id, bots_id)',
         'A warehouse mapping with the same ID in Bots already exists.'),
        ('bots_warehouse_single', 'unique(backend_id)',
         'Multiple warehouses per Bots backend is not currently supported.'),
    ]

class BotsStockInventory(orm.Model):
    _name = 'bots.stock.inventory'
    _inherit = 'bots.binding'
    _inherits = {'stock.inventory': 'openerp_id'}
    _description = 'Bots Inventory'

    _columns = {
        'openerp_id': fields.many2one('stock.inventory',
                                      string='Stock Inventory',
                                      required=True,
                                      ondelete='restrict'),
        'warehouse_id': fields.many2one('bots.warehouse',
                                      string='Bots Warehouse',
                                      required=True,
                                      ondelete='restrict'),
        }

    _sql_constraints = [
        ('bots_inventory_uniq', 'unique(backend_id, openerp_id)',
         'A Bots inventory already exists for this inventory for the same backend.'),
    ]

@bots
class BotsWarehouseBinder(BotsModelBinder):
    _model_name = [
            'bots.warehouse',
        ]

@bots
class BotsStockInventoryBinder(BotsModelBinder):
    _model_name = [
            'bots.stock.inventory',
        ]

@bots
class BotsWarehouseImport(ImportSynchronizer):
    _model_name = ['bots.warehouse']

    def import_picking_confirmation(self, model_name, record_id, picking_types=('in', 'out'), new_cr=True):
        """
        Import the picking confirmation from Bots
        """
        self.backend_adapter.get_picking_conf(model_name, record_id, picking_types, new_cr=new_cr)

    def import_picking_file(self, picking_types=('in', 'out'), file_data=None):
        """
        Import the picking confirmation from Bots
        """
        self.backend_adapter.process_data(picking_types, file_data)

    def import_stock_levels(self, warehouse_id, new_cr=True):
        """
        Import the picking confirmation from Bots
        """
        self.backend_adapter.get_stock_levels(warehouse_id, new_cr=new_cr)

@bots
class WarehouseAdapter(BotsCRUDAdapter):
    _model_name = 'bots.warehouse'

    def _handle_confirmations(self, cr, uid, stock_picking, prod_confirm, context=None):
        move_obj = self.session.pool.get('stock.move')
        picking_obj = self.session.pool.get('stock.picking')

        old_backorder_id = stock_picking.backorder_id and stock_picking.backorder_id.id or False
        moves_to_ship = {}
        query_move_ids = prod_confirm.keys()
        logger.info('Selecting stock move, picking, order, PO and pricelist info for stock moves %s', query_move_ids)
        cr.execute("""
            select
                sm.id "id", sm.product_id "product_id", sm.product_uom "product_uom", sm.prodlot_id "prodlot_id",
                sm.price_unit "price_unit", pl.currency_id "currency_id"
            from stock_move sm
            left outer join stock_picking sp on sp.id = sm.picking_id
            left outer join sale_order so on so.id = sp.sale_id
            left outer join purchase_order po on po.id = sp.purchase_id
            left outer join product_pricelist pl on pl.id = coalesce(so.pricelist_id, po.pricelist_id)
            where sm.id in %s
            """, [tuple(query_move_ids)])
        for move_item in cr.dictfetchall():
            qty = prod_confirm.get(move_item['id'], 0)
            moves_to_ship['move%s' % (move_item['id'])] = {
                'product_id': move_item['product_id'] or False,
                'product_qty': qty,
                'product_uom': move_item['product_uom'] or False,
                'prodlot_id': move_item['prodlot_id'] or False,
                'product_price' : move_item['price_unit'] or 0.0,
                'product_currency' : move_item['currency_id'] or False,
            }
        split = picking_obj.do_partial(cr, uid, [stock_picking.id], moves_to_ship, context=context)

        return split, old_backorder_id

    def _handle_cancellations(self, cr, uid, bots_stock_picking, prod_cancel, context=None):
        picking_obj = self.session.pool.get('stock.picking')
        stock_move_obj = self.session.pool.get('stock.move')
        procurement_obj = self.session.pool.get('procurement.order')
        sale_line_obj = self.session.pool.get('sale.order.line')
        wf_service = netsvc.LocalService("workflow")

        stock_picking = picking_obj.browse(cr, uid, bots_stock_picking.openerp_id.id, context=context)
        # If there are any cancellations we need to reset them back to confirmed so they are re-procured
        if prod_cancel:

            confirm_moves = False

            if stock_picking.sale_id:
                search_domain = [('sale_id','=',stock_picking.sale_id.id), ('state','=','confirmed')]
                pick_ids = picking_obj.search(cr, uid, search_domain, context=context)
                new_picking_id = pick_ids and pick_ids[0] or False

            if new_picking_id:
                # The picking's already confirmed, so we'll need to explicitly confirm the move.
                confirm_moves = True
            else:
                # Duplicate the entire picking including moves lines and procurements
                new_picking_id = picking_obj.copy(cr, uid, stock_picking.id, {'move_lines': []}, context=context)

            new_picking = picking_obj.browse(cr, uid, new_picking_id, context=context)
            moves = []

            events_orig = stock_picking.wms_disable_events
            if not events_orig:
                stock_picking.write({'wms_disable_events': True})
            # For the original picking remove lines which were cancelled
            for move in stock_picking.move_lines:
                if move.state == 'cancel' and move.product_id.id in prod_cancel: # If we were cancelled in OpenERP already then stay cancelled
                    prod_cancel[move.product_id.id] = prod_cancel[move.product_id.id] - move.product_qty
                    continue
                elif move.state == 'done': # This move is already completed so cannot be cancelled
                    continue
                if prod_cancel.get(move.product_id.id, 0) >= move.product_qty:
                    prod_cancel[move.product_id.id] = prod_cancel[move.product_id.id] - move.product_qty
                    procurement_id = procurement_obj.search(cr, uid, [('move_id', '=', move.id)], context=context)
                    new_move = stock_move_obj.copy(cr, uid, move.id, {'picking_id': new_picking_id}, context=context)
                    moves.append(new_move)
                    new_procurement_id = False
                    if procurement_id:
                        procurement = procurement_obj.browse(cr, uid, procurement_id[0], context=context)
                        cut_off = procurement.purchase_id and getattr(procurement.purchase_id, 'bots_cut_off', False) and procurement.purchase_id.bots_cut_off
                        # Context is not available in the workflow so we need to temporarily allow changes to the PO
                        # by lifting the cut-off flag instead
                        if cut_off:
                            procurement.purchase_id.write({'bots_cut_off': False})
                        new_note = ''
                        # Remove '_mto_to_mts_done_' from the new procurement note, so it will be allocated to stock
                        # by the scheduler if stock is available
                        if procurement.note:
                            new_note = procurement.note.replace('_mto_to_mts_done_', '')
                        defaults = {'move_id': new_move, 'purchase_id': False, 'note': new_note}
                        if move.sale_line_id:
                            defaults['procure_method'] = move.sale_line_id.type
                        new_procurement_id = procurement_obj.copy(cr, uid, procurement_id[0], defaults, context=context)
                        wf_service.trg_validate(uid, 'procurement.order', new_procurement_id, 'button_confirm', cr)
                        # Update SO lines to use new_procurement_id to avoid workflow moving to exception
                        sol_ids = sale_line_obj.search(cr, uid, [('procurement_id', '=', procurement_id[0])], context=context)
                        if sol_ids:
                            sale_line_obj.write(cr, uid, sol_ids, {'procurement_id': new_procurement_id}, context=context)

                    move.action_cancel()
                    cr.execute('SAVEPOINT procurement')
                    try: # Attempt to remove old procurement and allocate new one if there is space.
                        if new_procurement_id and procurement.purchase_id: # Add the new procurement back into the same PO if it came from one
                            procurement_obj.write(cr, uid, [new_procurement_id], {'purchase_id': procurement.purchase_id.id}, context=context)
                            wf_service.trg_validate(uid, 'procurement.order', new_procurement_id, 'button_check', cr)
                    except osv.except_osv, e: # No space, so we just cancel the old procurement and continue
                        cr.execute('ROLLBACK TO SAVEPOINT procurement')
                    finally:
                        cr.execute('RELEASE SAVEPOINT procurement')

                    if procurement_id and cut_off:
                        procurement.purchase_id.write({'bots_cut_off': True})

                elif prod_cancel.get(move.product_id.id, 0) > 0:
                    new_qty = prod_cancel.get(move.product_id.id, 0)
                    reduce_qty = move.product_qty - new_qty
                    prod_cancel[move.product_id.id] = prod_cancel[move.product_id.id] - new_qty
                    move.write({'product_qty': reduce_qty, 'product_uos_qty': reduce_qty})
                    procurement_id = procurement_obj.search(cr, uid, [('move_id', '=', move.id)], context=context)
                    procurement_obj.write(cr, uid, procurement_id, {'product_qty': reduce_qty, 'product_uos_qty': reduce_qty}, context=context)

                    new_move = stock_move_obj.copy(cr, uid, move.id, {'picking_id': new_picking_id, 'product_qty': new_qty, 'product_uos_qty': new_qty}, context=context)
                    moves.append(new_move)
                    if procurement_id:
                        new_note = ''
                        # Remove '_mto_to_mts_done_' from the new procurement note, so it will be allocated to stock
                        # by the scheduler if stock is available
                        existing_procurement = procurement_obj.browse(cr, uid, procurement_id[0], context=context)
                        if existing_procurement.note:
                            new_note = existing_procurement.note.replace('_mto_to_mts_done_', '')
                        defaults = {'move_id': new_move, 'purchase_id': False, 'product_qty': new_qty, 'product_uos_qty': new_qty, 'note': new_note}
                        if move.sale_line_id:
                            defaults['procure_method'] = move.sale_line_id.type
                        new_procurement_id = procurement_obj.copy(cr, uid, procurement_id[0], defaults, context=context)
                        wf_service.trg_validate(uid, 'procurement.order', new_procurement_id, 'button_confirm', cr)
                        # Update SO lines to use new_procurement_id which will likely be completed after the origional one
                        sol_ids = sale_line_obj.search(cr, uid, [('procurement_id', '=', procurement_id[0])], context=context)
                        if sol_ids:
                            sale_line_obj.write(cr, uid, sol_ids, {'procurement_id': new_procurement_id}, context=context)
                else:
                    pass

            if not events_orig:
                stock_picking.write({'wms_disable_events': events_orig})

            if moves and confirm_moves:
                stock_move_obj.action_confirm(cr, uid, moves, context=context)
            if moves: # Run this anyway if we need to confirm the picking or not, since it is a workflow there is no harm in emitting the signal
                add_checkpoint(self.session, 'stock.picking', new_picking_id, self.backend_record.id)
                wf_service.trg_validate(uid, 'stock.picking', new_picking_id, 'button_confirm', cr)
                if stock_picking.type == 'out' and stock_picking.sale_id:
                    wf_service.trg_validate(uid, 'sale.order', stock_picking.sale_id.id, 'ship_corrected', cr)
                wf_service.trg_write(uid, 'stock.picking', stock_picking.id, cr)
            elif not confirm_moves: # If no moves were backordered and we created a new picking, then unlink it
                picking_obj.unlink(cr, uid, [new_picking_id], context=context)

            return new_picking_id
        else:
            return False

    def _handle_backorder(self, cr, uid, stock_picking, bots_picking_id, split, old_backorder_id, context=None):
        res = {}

        picking_obj = self.session.pool.get('stock.picking')
        if stock_picking.type == 'in':
            picking_binder = self.get_binder_for_model('bots.stock.picking.in')
            bots_picking_obj = self.session.pool.get('bots.stock.picking.in')
        elif stock_picking.type == 'out':
            picking_binder = self.get_binder_for_model('bots.stock.picking.out')
            bots_picking_obj = self.session.pool.get('bots.stock.picking.out')

        stock_picking = bots_picking_obj.browse(cr, uid, stock_picking.id, context=context)

        # If there is a backorder, we need to assert that the current picking remains available
        # The backorder should be flagged for a checkpoint
        if stock_picking.backorder_id and not stock_picking.backorder_id.id == old_backorder_id:
            if stock_picking.backorder_id.state != 'done' and stock_picking.state != 'assigned':
                raise JobError('Error while creating backorder for picking %s imported from Bots' % (stock_picking.name,))

            if stock_picking.backend_id.feat_reexport_backorder:
                # 3PLs such as DSV assume that once they confirm delivery of part of the order, the remaining items should be ignored
                # Because of this we need to re-export the remaining undelivered stock as part of a backorder so it gets delivered
                backorder_picking_id = bots_picking_obj.search(cr, uid, [('openerp_id', '=', stock_picking.backorder_id.id)], context=context)
                picking_binder.unbind(stock_picking.id)
                picking_binder.bind(bots_picking_id, backorder_picking_id)
                picking_obj.action_assign_wkf(cr, uid, [stock_picking.openerp_id.id], context=context)
            else:
                # For other 3PLs which will continue to deliver the remaining outstanding items we should take no action
                # The remaining items keep using the same Bots ID and subsequent confirmations should be for this ID
                pass

            res.update({'stock.picking': [stock_picking.openerp_id.id]})

            add_checkpoint(self.session, stock_picking.openerp_id._name, stock_picking.openerp_id.id, self.backend_record.id)

        elif split == False: # We skipped the partial picking as there was nothing to pick
            res.update({'stock.picking': [stock_picking.openerp_id.id]})

        return res

    def _handle_additional_done_incoming(self, cr, uid, picking_id, product_qtys, context=None):
        if context == None:
            context = {}

        picking_obj = self.session.pool.get('stock.picking')
        move_obj = self.session.pool.get('stock.move')
        purchase_obj = self.session.pool.get('purchase.order')
        purchase_line_obj = self.session.pool.get('purchase.order.line')

        picking_old = picking_obj.browse(cr, uid, picking_id, context=context)
        purchase = picking_old.purchase_id
        if not purchase:
            raise NotImplementedError("Unable to process unexpected incoming stock for %s: Not linked to a PO" % (picking_id,))

        picking_new_data = purchase_obj._prepare_order_picking(cr, uid, purchase, context=context)
        picking_new_data.update({'wms_disable_events': True})
        picking_new_id = picking_obj.create(cr, uid, picking_new_data, context=context)

        prod_confirm = {}
        for product_id, qty in product_qtys:
            pol_data = purchase_line_obj._generate_purchase_line(cr, uid, product_id, qty, purchase.pricelist_id.id, purchase.partner_id.id, purchase.minimum_planned_date, context=context)
            pol_data.update({'order_id': purchase.id,
                             'state': 'confirmed'})
            purchase_line_id = purchase_line_obj.create(cr, uid, pol_data, context=context)
            purchase_line = purchase_line_obj.browse(cr, uid, purchase_line_id, context=context)

            move_data = purchase_obj._prepare_order_line_move(cr, uid, purchase, purchase_line, picking_new_id, context=context)
            move_id = move_obj.create(cr, uid, move_data, context=context)
            prod_confirm[move_id] = qty

        picking_obj.draft_force_assign(cr, uid, [picking_new_id])
        picking_obj.write(cr, uid, [picking_new_id], {'wms_disable_events': False}, context=context)
        picking_new = picking_obj.browse(cr, uid, picking_new_id, context=context)

        self._handle_confirmations(cr, uid, picking_new, prod_confirm, context=None)
        return True

    def _save_tracking(self, cr, uid, picking_json, picking, context=None):
        carrier_obj = self.session.pool.get('delivery.warehouse.carrier')
        picking_obj = self.session.pool.get('stock.picking')
        sale_obj = self.session.pool.get('sale.order')
        carrier_tracking_obj = self.session.pool.get('stock.picking.carrier.tracking')

        tracking_number = picking_json.get('tracking_number')
        carrier = picking_json.get('carrier')

        if not tracking_number and not carrier:
            return

        if tracking_number and not carrier:
            if tracking_number == NOT_TRACKED:
                carrier = BLANK_LABEL
            else:
                raise Exception('Tracking reference found but no carrier code')

        if carrier and not tracking_number:
            raise Exception('Carrier code found but no tracking reference')

        warehouse_carrier_id = None

        if carrier:
            carrier_ids = carrier_obj.search(cr, uid, [('carrier_code', 'like', carrier)], context=context)
            if carrier_ids:
                warehouse_carrier_id = carrier_ids[0]

        if not warehouse_carrier_id:
            raise JobError('Carrier %s is not recognised by Odoo' % carrier)

        picking_obj.write(cr, uid, picking.id, {'carrier_tracking_ref': tracking_number}, context=context)

        # Save each tracking number on it's own line
        tracking_number = tracking_number.split(',')

        for number in tracking_number:

            tracking_id = carrier_tracking_obj.create(
                cr, uid, {'picking_id': picking.id, 'tracking_reference': number, 'carrier_id': warehouse_carrier_id}
            )

            tracking = carrier_tracking_obj.browse(cr, uid, tracking_id)
            tracking_url = tracking.tracking_link

            picking_obj.message_post(
                cr, uid, picking.id, body='Tracking Reference: ' + tracking_url, context=context
            )

            sale_obj.message_post(
                cr, uid, picking.sale_id.id,
                body='Delivery Order: %s <br><br>Tracking Reference: %s' % (picking.name, tracking_url),
                context=context
            )

        return True

    def get_main_picking(self, main_picking, ctx):
        """ Determines the correct picking to use for dropship orders in the case where a picking has split.

            If a picking is split for dropship orders it will cause a problem because the bots id that the supplier
            has will be different to the bots id on the new backorder delivery order and we can't send them the new
            bots id like we can do for the warehouse crossdock procedure.

            The correct picking in these cases is determined by following the pickings from the main picking using the
            backorder_id link until we get to the final one
        """

        _cr = self.session.cr
        picking_obj = self.session.pool.get('stock.picking')
        bots_picking_out_obj = self.session.pool.get('bots.stock.picking.out')

        search_id = main_picking.openerp_id.id
        backorder_id = None

        while True:
            search_id = picking_obj.search(_cr, self.session.uid, [('backorder_id', '=', search_id)])

            if not search_id:
                break
            else:
                backorder_id = search_id

        if backorder_id:
            backorder_id = picking_obj.browse(_cr, self.session.uid, backorder_id[0])

            bots_backorder_id = bots_picking_out_obj.search(
                _cr, self.session.uid, [('openerp_id', '=', backorder_id.id)]
            )[0]
            return bots_picking_out_obj.browse(_cr, self.session.uid, bots_backorder_id, context=ctx)

        return main_picking

    def get_picking_conf(self, model_name, record_id,  picking_types, new_cr=True):

        exceptions = []

        FILENAME = r'^picking_conf_.*\.json$'
        file_ids = self._search(FILENAME)
        res = []

        for file_id in file_ids:
            try:
                with file_to_process(self.session, file_id[0], new_cr=new_cr) as f:
                    import_picking_file.delay(self.session, model_name, record_id, picking_types, bots_file_name=file_id[1], file_data=json.load(f))

            except OperationalError, e:
                # FILE_LOCK_MSG suggests that another job is already handling these files,
                # so it is safe to continue without any further action.
                if e.message and FILE_LOCK_MSG in e.message:
                    exception = "Exception %s when processing file %s: %s" % (e, file_id[1], traceback.format_exc())
                    exceptions.append(exception)
            except Exception, e:
                # Log error then continue processing files
                exception = "Exception %s when processing file %s: %s" % (e, file_id[1], traceback.format_exc())
                exceptions.append(exception)
                pass

        # If we hit any errors, fail the job with a list of all errors now
        if exceptions:
            raise JobError('The following exceptions were encountered:\n\n%s' % ('\n\n'.join(exceptions),))

        return res

    def process_data(self, picking_types, picking_data):

        product_binder = self.get_binder_for_model('bots.product')
        picking_in_binder = self.get_binder_for_model('bots.stock.picking.in')
        picking_out_binder = self.get_binder_for_model('bots.stock.picking.out')
        bots_picking_in_obj = self.session.pool.get('bots.stock.picking.in')
        bots_picking_out_obj = self.session.pool.get('bots.stock.picking.out')
        move_obj = self.session.pool.get('stock.move')
        picking_obj = self.session.pool.get('stock.picking')

        ctx = self.session.context.copy()
        ctx['wms_bots'] = True

        _cr = self.session.cr

        picking_data = picking_data if type(picking_data) in (list, tuple) else [picking_data, ]
        for pickings in picking_data:
            for picking in pickings['orderconf']['shipment']:
                if picking['type'] not in picking_types:
                    # We are not a picking we want to import, discarded
                    continue

                if picking['type'] == 'in':
                    picking_binder = picking_in_binder
                    bots_picking_obj = bots_picking_in_obj
                elif picking['type'] == 'out':
                    picking_binder = picking_out_binder
                    bots_picking_obj = bots_picking_out_obj
                else:
                    raise NotImplementedError("Unable to import picking of type %s" % (picking['type'],))

                # Map the picking by the Bots ID/Order Number - This is used if mapping fails with the move ids
                main_picking_id = picking_binder.to_openerp(picking['id'])
                if not main_picking_id:
                    raise NoExternalId("Picking %s could not be found in OpenERP" % (picking['id'],))
                main_picking = bots_picking_obj.browse(_cr, self.session.uid, main_picking_id, context=ctx)
                openerp_id = main_picking.openerp_id

                # If a dropship picking has been split then the main picking is the original picking, not the backorder
                # So we need to determine the correct picking to use
                if picking['type'] == 'out' and openerp_id.sp_dropship and len(openerp_id.sale_id.picking_ids) > 1:
                    main_picking = self.get_main_picking(main_picking, ctx)

                if picking['type'] == 'in':
                    logger.info("Main picking for PO %s: %s", picking['id'], main_picking_id)
                    open_shipments = filter(
                        lambda x: x.state == 'assigned',
                        main_picking.openerp_id.purchase_id.picking_ids
                    )

                    if not open_shipments:
                        raise Exception(
                            'PO %s is already received' % main_picking.openerp_id.purchase_id.name
                        )

                picking_ids = [main_picking.openerp_id.id]
                ctx.update({'company_id': main_picking.openerp_id.company_id.id})

                move_dict = {}
                moves_extra = {}

                product_external_ids = [line['product'] for line in picking['line']]
                product_external_dict = product_binder.to_openerp_multi(product_external_ids)
                for line in picking['line']:
                    # Handle products and qtys
                    product_id = product_external_dict.get(line['product'], False)
                    if not product_id:
                        raise NoExternalId("Product %s could not be found in OpenERP" % (line['product'],))
                    qty = int(float('qty_real' in line and line['qty_real'] or line['uom_qty']))
                    ptype = line.get('status') or 'DONE'

                    ignore_states = ('cancel', 'draft', 'done', 'confirmed')
                    if ptype == 'CANCELLED':
                        ignore_states = ('draft', 'done')

                    # Attempt to find moves for this line
                    move_ids = [int(x) for x in line.get('move_ids', '').split(',') if x]
                    move_ids = move_obj.search(_cr, self.session.uid, [('id', 'in', move_ids),
                                                                       ('state', 'not in', ignore_states),
                                                                       ], context=ctx)

                    # Match moves from the main picking as a fallback
                    matching_moves = move_obj.search(_cr, self.session.uid,
                                                    [('picking_id', '=', main_picking.openerp_id.id),
                                                     ('product_id', '=', product_id),
                                                     ('state', 'not in', ignore_states),
                                                     ], context=ctx)
                    move_ids.extend(matching_moves)

                    if picking['type'] == 'in':
                        logger.info("Move(s) found for product %s and used for confirmation: %s", product_id, matching_moves)
                        if not matching_moves:
                            other_moves_for_product = move_obj.search(_cr, self.session.uid,
                                                    [('picking_id', 'in', main_picking.openerp_id.picking_ids),
                                                     ('product_id', '=', product_id),
                                                     ('state', 'not in', ignore_states),
                                                     ], context=ctx)
                            if other_moves_for_product:
                                logger.info("Move(s) with valid state found for product %s: %s", product_id, other_moves_for_product)
                            else:
                                logger.info("No valid stock moves found for product %s", product_id)

                    # Distribute qty over the moves, sperating by type - Use SQL to avoid slow name_get function
                    if move_ids:
                        _cr.execute("""select id "id", picking_id "picking_id", product_qty "product_qty", product_id "product_id"
                                    from stock_move where id in %s """, [tuple(move_ids), ])
                        res_dict = dict([(res['id'], res) for res in
                                         _cr.dictfetchall()])  # Convert to a dict to read them back in the correct order
                        for move_id in move_ids:
                            move = res_dict[move_id]
                            key = (move['id'], move['picking_id'], move['product_id'])
                            if qty and sum(move_dict.get(key, {}).values()) < move['product_qty']:
                                qty_to_add = min(move['product_qty'], qty)
                                qty -= qty_to_add
                                move_dict.setdefault(key, {})[ptype] = move_dict.setdefault(key, {}).get(ptype,
                                                                                                         0) + qty_to_add
                            if qty == 0:
                                break

                    # No moves found or unallocated qty, handle these separatly if possible
                    if qty:
                        moves_extra.setdefault(ptype, []).append((product_id, qty))

                # Group moves and qtys by pickings and type
                type_picking_move_dict = {}  # Dicts of move_id and qty
                type_picking_prod_dict = {}  # Dicts of product_id and qty
                for (move_id, picking_id, product_id), type_qtys in move_dict.iteritems():
                    if not picking_id:
                        raise NotImplementedError(
                            "Stock confirmation must be for a picking. Move ID %d with no picking are not supported" % (
                            move_id,))
                    if type_qtys and picking_id not in picking_ids:
                        picking_ids.append(picking_id)
                    for ptype, qty in type_qtys.iteritems():
                        key = (picking_id, ptype)
                        type_picking_move_dict.setdefault(key, {})[move_id] = type_picking_move_dict.get(key,
                                                                                                         {}).get(
                            move_id, 0) + qty
                        type_picking_prod_dict.setdefault(key, {})[product_id] = type_picking_prod_dict.get(key,
                                                                                                            {}).get(
                            product_id, 0) + qty
                del move_dict

                # If we are not confirming anything we should just update the tracking info and continue
                if picking['confirmed'] not in ('Y', 'True', '1', True, 1):
                    continue

                # Handle opperations
                backorders = []
                for (picking_id, ptype), moves_part in type_picking_move_dict.iteritems():

                    # Get the binding ID for this picking
                    openerp_picking = picking_obj.browse(_cr, self.session.uid, picking_id, context=ctx)
                    bots_picking_id = bots_picking_obj.search(_cr, self.session.uid,
                                                              [('openerp_id', '=', picking_id),
                                                               ('backend_id', '=', self.backend_record.id)],
                                                              context=ctx)
                    if bots_picking_id:
                        bots_picking_id = bots_picking_id[0]
                    if not bots_picking_id:
                        bots_picking_id = main_picking_id  # Fallback if not found

                    bots_picking = bots_picking_obj.browse(_cr, self.session.uid, bots_picking_id, context=ctx)

                    if ptype == 'DONE':
                        split, old_backorder_id = self._handle_confirmations(_cr, self.session.uid, openerp_picking,
                                                                             moves_part, context=ctx)
                        backorders.append((bots_picking, picking['id'], split, old_backorder_id))
                    elif ptype == 'CANCELLED':
                        self._handle_cancellations(_cr, self.session.uid, bots_picking,
                                                   type_picking_prod_dict.get((picking_id, ptype), {}), context=ctx)
                    elif ptype == 'RETURNED':  # TODO: Handle returns
                        raise NotImplementedError('Handling returned lines is not currently supported')
                    elif ptype == 'REFUNDED':  # TODO: Handle refunds
                        raise NotImplementedError('Handling refunded lines is not currently supported')
                    else:
                        raise NotImplementedError("Unable to process picking confirmation of type %s" % (ptype,))

                for picking_id in picking_ids:
                    bots_picking_id = bots_picking_obj.search(_cr, self.session.uid,
                                                              [('openerp_id', '=', picking_id),
                                                               ('backend_id', '=', self.backend_record.id)],
                                                              context=ctx)
                    if bots_picking_id:
                        bots_picking_id = bots_picking_id[0]
                    if not bots_picking_id:
                        bots_picking_id = main_picking_id  # Fallback if not found

                    bots_picking = bots_picking_obj.browse(_cr, self.session.uid, bots_picking_id, context=ctx)

                    if moves_extra.get('DONE') and picking['type'] == 'in':
                        # Any additional done stock should be added to an incoming PO
                        self._handle_additional_done_incoming(_cr, self.session.uid, picking_id,
                                                              moves_extra.get('DONE'), context=ctx)
                        del moves_extra['DONE']
                        if (picking_id,
                            'DONE') not in type_picking_move_dict:  # If this is the only additional stock then create a backorder for the origional
                            backorders.append((bots_picking, picking['id'], False, False))

                for bots_picking, picking_id, split, old_backorder_id in backorders:
                    self._handle_backorder(_cr, self.session.uid, bots_picking, picking_id, split, old_backorder_id,
                                           context=ctx)

                # Handle tracking information
                update_ids = [p_id for p_id in picking_ids if p_id != main_picking.openerp_id.id] or picking_ids
                assert len(update_ids) == 1, "We should only be working with one delivery order"
                update_id = update_ids[0]

                delivery_order = picking_obj.browse(_cr, self.session.uid, update_id, context=ctx)

                # Because of the weird way odoo does backorder delivery orders, we need this check to make sure
                # that the tracking number gets added to the last delivery order ( which is the original ) so that
                # magento gets correctly updated with everything shipped
                last_shipment = all([
                    delivery_order.backorder_id is not False,
                    delivery_order.state == 'done',
                    len(delivery_order.tracking_references) == 0
                ])

                if last_shipment:
                    delivered_picking = delivery_order
                else:
                    # If this was only a partial delivery then we want to use the backorder id of the picking as that is the delivery
                    # order that was actually delivered.
                    delivered_picking = delivery_order.backorder_id or delivery_order

                tracking_saved = self._save_tracking(
                    _cr, self.session.uid, picking, delivered_picking, context=ctx
                )
                if tracking_saved:
                    try:
                        export_tracking_number.delay(
                            self.session, 'magento.stock.picking.out', delivered_picking.magento_bind_ids[0].id
                        )
                    except IndexError:
                        # If the order has not come from Magento,
                        # behaviour to update magento is overriden, as the order will have originated elsewhere
                        if openerp_id.sale_id.magento_state != "Not a Magento order":
                            raise IndexError('Picking %s has no corresponding magento bind ids' % (delivered_picking.name,))

                # TODO: Handle various operations for extra stock (Additional done incoming for PO handled above)
                if moves_extra:
                    raise NotImplementedError(
                        "Unable to process unexpected stock for %s: %s" % (picking['id'], moves_extra,))

    def get_stock_levels(self, warehouse_id, new_cr=True):
        product_binder = self.get_binder_for_model('bots.product')
        inventory_binder = self.get_binder_for_model('bots.stock.inventory')
        bots_warehouse_obj = self.session.pool.get('bots.warehouse')
        product_obj = self.session.pool.get('product.product')
        inventory_obj = self.session.pool.get('stock.inventory')
        bots_inventory_obj = self.session.pool.get('bots.stock.inventory')
        exceptions = []

        FILENAME = r'^inventory_.*\.json$'
        file_ids = self._search(FILENAME)
        res = []
        warehouse = bots_warehouse_obj.browse(self.session.cr, self.session.uid, warehouse_id, self.session.context)


        for file_id in file_ids:
            try:
                with file_to_process(self.session, file_id[0], new_cr=new_cr) as f:
                    json_data = json.load(f)
                    _cr = self.session.cr

                    _session = ConnectorSession(self.session.cr, self.session.uid, context=self.session.context)
                    inventory_lines = {}
                    file_exceptions = []
                    create_inventory = False

                    json_data = json_data if type(json_data) in (list, tuple) else [json_data,]
                    for inventory in json_data:
                        for line in inventory['inventory']['inventory_line']:
                            product_id = product_binder.to_openerp(line['product'])
                            if not product_id:
                                file_exceptions.append(NoExternalId("Product %s could not be found in OpenERP" % (line['product'],)))
                                continue
                            # Check the stock level for this warehouse at this time
                            time = datetime.strptime(line['datetime'], '%Y-%m-%d %H:%M:%S')
                            if 'qty_total' in line and line['qty_total'].lstrip("-+").isdigit(): # Take the absolule stock in the warehouse
                                qty = int(line['qty_total'])
                            else: # Else if not available, work out from available + outgoing available
                                qty = int(line['qty_available'])
                                if 'qty_outgoing_available' in line and line['qty_outgoing_available'].isdigit():
                                    qty += int(line['qty_outgoing_available'])
                            if inventory_lines.setdefault(time.strftime(DEFAULT_SERVER_DATETIME_FORMAT), {}).get('product_id', None):
                                file_exceptions.append(AssertionError("Product %s, ID %s appears twice in the inventory for %s" % (line['product'], product_id, time)))
                                continue
                            inventory_lines.setdefault(time.strftime(DEFAULT_SERVER_DATETIME_FORMAT), {})[product_id] = qty

                    if file_exceptions:
                        raise AssertionError("Errors were encountered on inventory import:\n%s" % ("\n".join([repr(x) for x in file_exceptions])))

                    inventory_lines = sorted(inventory_lines.items(), key=lambda x: x[0])
                    for time, products in inventory_lines:
                        inventory = {
                                'name': 'Bots - %s - %s' % (self.backend_record.name, time,),
                                'date': time,
                                'company_id': warehouse.warehouse_id.company_id.id,
                                'inventory_line_id': [],
                            }
                        for product_id, qty in products.iteritems():
                            location_id = warehouse.warehouse_id.lot_stock_id.id
                            ctx = {
                                    'location': location_id,
                                    'compute_child': False,
                                    #'to_date': time, # FIXME: Any recent inventories, even backdated, will not be considered since the date is always when it is done. Core bug or feature?
                                }
                            prod = product_obj.browse(_cr, self.session.uid, product_id, context=ctx)

                            if int(qty) != int(prod.qty_available):
                                # We have a difference and will need to post an inventory
                                create_inventory = True

                            inventory_line = {
                                    'product_id': product_id,
                                    'location_id': location_id,
                                    'product_qty': int(qty),
                                    'product_uom': prod.uom_id.id, # We assume the qty is always in the standard UoM
                                }
                            inventory['inventory_line_id'].append([0, False, inventory_line])

                        if create_inventory:
                            # We have a difference in inventory so we must create and validate a new inventory
                            inventory_id = inventory_obj.create(_cr, self.session.uid, inventory, context=self.session.context)
                            # Prevent automatic completion of imported physical inventories
                            # --> ensures they are manually validated before making stock corrections
                            #inventory_obj.action_confirm(_cr, self.session.uid, [inventory_id], context=self.session.context)
                            #inventory_obj.action_done(_cr, self.session.uid, [inventory_id], context=self.session.context)
                            binding_id = bots_inventory_obj.create(_cr, self.session.uid,
                                {'backend_id': self.backend_record.id,
                                'openerp_id': inventory_id,
                                'warehouse_id': warehouse.id,
                                'bots_id': '%s %s' % (self.backend_record.name, time,),})
                            add_checkpoint(_session, 'stock.inventory', inventory_id, self.backend_record.id)

            except OperationalError, e:
                # FILE_LOCK_MSG suggests that another job is already handling these files,
                # so it is safe to continue without any further action.
                if e.message and FILE_LOCK_MSG in e.message:
                    exception = "Exception %s when processing file %s: %s" % (e, file_id[1], traceback.format_exc())
                    exceptions.append(exception)
            except Exception, e:
                # Log error then continue processing files
                exception = "Exception %s when processing file %s: %s" % (e, file_id[1], traceback.format_exc())
                exceptions.append(exception)
                pass

        # If we hit any errors, fail the job with a list of all errors now
        if exceptions:
            raise JobError('The following exceptions were encountered:\n\n%s' % ('\n\n'.join(exceptions),))

        return res

@job
def import_stock_levels(session, model_name, record_id, new_cr=True):
    warehouse = session.browse(model_name, record_id)
    backend_id = warehouse.backend_id.id
    env = get_environment(session, model_name, backend_id)
    warehouse_importer = env.get_connector_unit(BotsWarehouseImport)
    warehouse_importer.import_stock_levels(record_id, new_cr=new_cr)
    return True


@job
def import_picking_confirmation(session, model_name, record_id, picking_types, new_cr=True):
    warehouse = session.browse(model_name, record_id)
    backend_id = warehouse.backend_id.id
    env = get_environment(session, model_name, backend_id)
    warehouse_importer = env.get_connector_unit(BotsWarehouseImport)
    warehouse_importer.import_picking_confirmation(model_name, record_id, picking_types=picking_types, new_cr=new_cr)
    return True


@job
def import_picking_file(session, model_name, record_id, picking_types, bots_file_name=None, file_data=None):
    warehouse = session.browse(model_name, record_id)
    backend_id = warehouse.backend_id.id
    env = get_environment(session, model_name, backend_id)
    warehouse_importer = env.get_connector_unit(BotsWarehouseImport)
    try:
        warehouse_importer.import_picking_file(picking_types=picking_types, file_data=file_data)
    except Exception, e:
        exception = "Exception %s when processing file %s: %s" % (e, bots_file_name, traceback.format_exc())
        raise Exception(exception)
    return True
