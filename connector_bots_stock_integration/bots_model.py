# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright 2015 credativ Ltd
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

from openerp.osv import fields, orm
from openerp.addons.connector.queue.job import job
from openerp.addons.connector_bots.connector import get_environment
from openerp.addons.connector.unit.synchronizer import ExportSynchronizer
from openerp.addons.connector.session import ConnectorSession

from openerp.addons.connector_bots.unit.backend_adapter import BotsCRUDAdapter
from openerp.addons.connector_bots.backend import bots

from .supplier_stock import import_supplier_stock

import json
from datetime import datetime


class BotsBackend(orm.Model):
    _inherit = 'bots.backend'    

    def _scheduler_import_supplier_stock(self, cr, uid, domain=None, new_cr=True, context=None):
        self._bots_backend(cr, uid, self.import_supplier_stock, domain=domain, context=context)
        
    def import_supplier_stock(self, cr, uid, ids, new_cr=True, context=None):
        """ Import Supplier Stock """
        if not hasattr(ids, '__iter__'):
            ids = [ids]
        backend_ids = self.search(cr, uid, [('name', '=', 'SUPPLIERS STOCK')], context=context)
        backend_id = backend_ids and backend_ids[0] or False
        if backend_id:
           session = ConnectorSession(cr, uid, context=context)
           import_supplier_stock.delay(
                    session, 'Supplier Stock',backend_id, new_cr=new_cr, priority=5
                )
        return True

