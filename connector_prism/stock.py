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

import json
import logging
from datetime import datetime

from psycopg2 import IntegrityError

from openerp.tools.translate import _
from openerp.addons.connector.queue.job import job
from openerp.addons.connector.exception import JobError, NoExternalId, MappingError, RetryableJobError
from openerp.addons.connector_bots.backend import bots
from openerp.addons.connector_bots.connector import get_environment
from openerp.addons.connector_bots.stock import (StockPickingOutAdapter, StockPickingInAdapter, BotsPickingExport)


logger = logging.getLogger(__name__)


@bots(replacing=StockPickingInAdapter)
class PrismPickingInAdapter(StockPickingInAdapter):
    _picking_type = None
    _model_name = 'bots.stock.picking.in'
    _picking_type = 'in'

    def _prepare_create_data(self, picking_id):
        data, FILENAME, bots_id = super(PrismPickingInAdapter, self)._prepare_create_data(picking_id)

        move_obj = self.session.pool.get('stock.move')

        cross_dock = 0
        for line in data['picking']['pickings'][0].get('line', []):
            if line.get('move_id'):
                move = move_obj.browse(self.session.cr, self.session.uid, line.get('move_id'))
                cross_dock = move.purchase_line_id.order_id.bots_cross_dock and 1 or 0

        data['picking']['pickings'][0].update({'crossdock': cross_dock})
        return data, FILENAME, bots_id

@bots(replacing=StockPickingOutAdapter)
class PrismPickingOutAdapter(StockPickingOutAdapter):
    _picking_type = None
    _model_name = 'bots.stock.picking.out'
    _picking_type = 'out'

    def _prepare_create_data(self, picking_id):
        data, FILENAME, bots_id = super(PrismPickingOutAdapter, self)._prepare_create_data(picking_id)

        move_obj = self.session.pool.get('stock.move')

        for line in data['picking']['pickings'][0].get('line', []):
            if line.get('move_id'):
                move = move_obj.browse(self.session.cr, self.session.uid, line.get('move_id'))
                line.update({'customs_commodity_code': move.product_id.magento_commodity_code or '0',})

        return data, FILENAME, bots_id


@bots(replacing=BotsPickingExport)
class PrismBotsPickingExport(BotsPickingExport):

    def run(self, binding_id):
        # Check if we are a PO edit and if the original already has a binding - use this instead if PO edits are not supported
        if self.model._name == 'bots.stock.picking.in' and not self.backend_record.feat_picking_in_cancel:
            picking = self.model.browse(self.session.cr, self.session.uid, binding_id).openerp_id
            if picking.purchase_id and picking.purchase_id.order_edit_id:
                old_binding_ids = self.model.search(self.session.cr, self.session.uid, [('purchase_id', '=', picking.purchase_id.order_edit_id.id), ('bots_id', '!=', False)])
                if old_binding_ids:
                    bots_id = self.model.browse(self.session.cr, self.session.uid, old_binding_ids).bots_id
                    self.model.unlink(self.session.cr, self.session.uid, old_binding_ids)
                    self.model.write(self.session.cr, self.session.uid, old_binding_ids, {'bots_id': bots_id})
                    return
        # Else create a new binding
        return super(PrismBotsPickingExport, self).run(binding_id)

