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

from openerp.osv import orm, fields
from openerp.tools.translate import _

from openerp.addons.connector_bots.product import BotsProductBinder
from openerp.addons.connector_bots.backend import bots
from openerp.addons.connector.exception import MappingError

import logging
_logger = logging.getLogger(__name__)

class ProductProduct(orm.Model):
    _inherit = 'product.product'

    _columns = {
        'magento_prism_sku': fields.char('Prism SKU', select=1),
        'magento_commodity_code': fields.char('Customs Commodity Code'),
    }

@bots(replacing=BotsProductBinder)
class PrismProductBinder(BotsProductBinder):
    _model_name = [
            'bots.product',
        ]

    def to_openerp(self, external_id, unwrap=False):
        '''Match the SKU from Bots to the OpenERP product. Since product master sync is not yet implemented we will attempt to match directly on SKU with overrides'''
        # Attempt to get overriding mappings
        bots_product_ids = self.session.search('bots.product', [('bots_id', '=', str(external_id)), ('backend_id', '=', self.backend_record.id)])
        openerp_ids = []
        if bots_product_ids:
            bots_product_data = self.session.read('bots.product', bots_product_ids, ['product_id'])
            openerp_ids = [x['product_id'][0] for x in bots_product_data]
        else:
            # If no overriding mappings, try to match the SKU directly
            openerp_ids = self.session.search('product.product', [('magento_prism_sku', '=', str(external_id))])
            #if not binding_ids: # Do not default to default_code, only use the value imported from 3rd party site (magento_prism_sku)
            #    binding_ids = self.session.search('product.product', [('default_code', '=', str(external_id))])
        if not openerp_ids:
            return None
        if len(openerp_ids) > 1:
            _logger.warning('Found multiple OpenERP IDs for product with Bots SKU %s' % (external_id,))
        openerp_id = openerp_ids[0]
        return openerp_id

    def to_openerp_multi(self, external_ids, unwrap=False):
        '''Match list of SKUs from Bots and return a dict of {External_ID: OpenERP_ID}'''
        external_ids = [str(xid) for xid in external_ids]
        res = dict.fromkeys(external_ids, False)
        bots_product_ids = self.session.search('bots.product', [('bots_id', 'in', external_ids), ('backend_id', '=', self.backend_record.id)])
        if bots_product_ids:
            bots_product_data = self.session.read('bots.product', bots_product_ids, ['bots_id', 'openerp_id'])
            for bots_product in bots_product_data:
                res[bots_product['bots_id']] = bots_product['id']

        # For any with no mappings attempt to map directly to OpenERP SKU
        external_ids = [xid for (xid, oeid) in res.iteritems() if oeid == False]
        if external_ids:
            product_ids = self.session.search('product.product', [('magento_prism_sku', 'in', external_ids)])
            product_data = self.session.read('product.product', product_ids, ['magento_prism_sku'])
            for product in product_data:
                res[product['magento_prism_sku']] = product['id']

        return res

        # For any with no mappings attempt to map directly to OpenERP SKU
        external_ids = [xid for (xid, oeid) in res.iteritems() if oeid == False]
        if external_ids:
            bots_product_ids = self.session.search('bots.product', [('default_code', 'in', external_ids), ('backend_id', '=', self.backend_record.id)])
            bots_product_data = self.session.read('bots.product', bots_product_ids, ['bots_id', 'openerp_id'])
            for bots_product in bots_product_data:
                if unwrap:
                    res[bots_product['bots_id']] = bots_product['openerp_id'][0]
                else:
                    res[bots_product['bots_id']] = bots_product['id']

        return res

    def to_backend(self, record_id, wrap=False):
        '''Export the SKU to Bots from the OpenERP product. Since product master sync is not yet implimented we will attempt to match directly on SKU with overrides'''

        # Attempt to get overriding mappings
        bots_product_ids = self.session.search('bots.product', [('product_id', '=', record_id), ('backend_id', '=', self.backend_record.id)])
        if bots_product_ids:
            bots_record = self.session.read('bots.product', bots_product_ids[0], ['bots_id'])['bots_id']
        else:
            values = self.session.read("product.product", record_id, ['default_code', 'magento_prism_sku'])
            bots_record = values.get('magento_prism_sku') #or values.get('default_code') # Do not default to default_code, only use the value imported from 3rd party site (magento_prism_sku)
            if not bots_record:
                raise MappingError(_('Missing PRISM product mapping for product %s [%s].') % (values.get('default_code'), record_id,)) 
        return bots_record
