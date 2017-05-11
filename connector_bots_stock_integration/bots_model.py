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

from openerp.osv import orm
from openerp.addons.connector.session import ConnectorSession

from .supplier_stock import import_supplier_stock


class BotsBackend(orm.Model):
    _inherit = 'bots.backend'    

    def _scheduler_import_supplier_stock(self, cr, uid, domain=None, context=None):

        domain = domain or [('name', '=', 'Supplier Stock')]
        try:
            backend_id = self.search(cr, uid, domain, context=context)[0]
        except IndexError:
            raise Exception('Bots backend for Supplier Stock not found')
        else:
            self.import_supplier_stock(cr, uid, backend_id, context=context)

    def import_supplier_stock(self, cr, uid, backend_id, new_cr=True, context=None):
        """ Import Supplier Stock """

        session = ConnectorSession(cr, uid, context=context)

        import_supplier_stock.delay(session, 'bots.backend.supplier.feed', backend_id, priority=5)

        return True

