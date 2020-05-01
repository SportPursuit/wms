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
from openerp.addons.connector_bots.stock import (BotsPickingExport)


logger = logging.getLogger(__name__)


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
