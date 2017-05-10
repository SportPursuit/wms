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

import csv
import itertools
import logging
from cStringIO import StringIO
from openerp.osv import orm, fields, osv
from openerp import pooler, netsvc, SUPERUSER_ID
from openerp.tools.translate import _
from openerp.tools.misc import DEFAULT_SERVER_DATETIME_FORMAT

from openerp.addons.connector.session import ConnectorSession
from openerp.addons.connector.queue.job import job
from openerp.addons.connector.exception import JobError, NoExternalId
from openerp.addons.connector.unit.synchronizer import ImportSynchronizer
from openerp.addons.magentoerpconnect.stock_tracking import export_tracking_number

from openerp.addons.connector_bots.unit.binder import BotsModelBinder
from openerp.addons.connector_bots.unit.backend_adapter import BotsCRUDAdapter, file_to_process
from openerp.addons.connector_bots.backend import bots
from openerp.addons.connector_bots.connector import get_environment, add_checkpoint
import openerp.addons.decimal_precision as dp
import json
import traceback
from datetime import datetime

from psycopg2 import OperationalError
_logger = logging.getLogger(__name__)

@bots
class BotsStockImport(ImportSynchronizer):
    _model_name = ['bots.product']

    def import_supplier_stock(self):
        """
        Import the picking confirmation from Bots
        """
        self.backend_adapter.get_supplier_stock()

@bots
class StockAdapter(BotsCRUDAdapter):
    _model_name = 'bots.product'
        
    def _check_product(self, cr, barcode=False, sku=False):
        #cr = pooler.get_db(self.session.cr.dbname).cursor()
        if barcode and sku:
            return self.session.pool.get('product.product').search(cr, SUPERUSER_ID, [('magento_barcode','=',barcode), ('magento_supplier_sku','=',sku)])
        return False
        
    def _check_vendor(self, cr, supplier_id=False):
        #cr = pooler.get_db(self.session.cr.dbname).cursor()
        if supplier_id:
            return self.session.pool.get('res.partner').search(cr, SUPERUSER_ID, [('ref','=',supplier_id), ('supplier','=',True)])
        return False
        
    def _raise_error(self, filename=False, err_barcode=None, err_sku=None,err_supplier=None, err_barcode_multi=None, err_sku_multi=None):
        if err_barcode or err_sku:
            if err_barcode_multi or err_sku_multi:
                if err_supplier:
                    _logger.error('Error while processing the File %s : Product(s) with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" does not Exists, Product(s) with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" appeared more than Once and Supplier(s) With Reference(s) "%s" does not Exists.', filename, err_barcode, err_sku, err_barcode_multi, err_sku_multi, err_supplier) 
                    raise JobError('Error while processing the File %s : Product with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" does not Exists, Product(s) with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" appeared more than Once and Supplier(s) With Reference(s) "%s" does not Exists.' % (filename, err_barcode,err_sku, err_barcode_multi, err_sku_multi, err_supplier,))
                else:
                    _logger.error('Error while processing the File %s : Product(s) with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" does not Exists and Product(s) with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" appeared more than Once.', filename, err_barcode, err_sku, err_barcode_multi, err_sku_multi) 
                    raise JobError('Error while processing the File %s : Product(s) with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" does not Exists and Product(s) with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" appeared more than Once.' % (filename, err_barcode,err_sku, err_barcode_multi, err_sku_multi))
            else:
                _logger.error('Error while processing the File %s : Product(s) with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" does not Exists.',filename, err_barcode, err_sku) 
                raise JobError('Errors while processing the File %s : Product(s) with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" does not Exists.' % (filename,err_barcode, err_sku,))                    
        elif err_barcode_multi or err_sku_multi:
            if err_supplier:
                _logger.error('Error while processing the File %s : Product(s) with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" appeared more than Once and Supplier(s) With Reference(s) "%s" does not Exists.', filename, err_barcode_multi, err_sku_multi, err_supplier) 
                raise JobError('Error while processing the File %s : Product(s) with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" appeared more than Once and Supplier(s) With Reference(s) "%s" does not Exists.' % (filename, err_barcode_multi, err_sku_multi, err_supplier,))
            else:
                _logger.error('Error while processing the File %s : Product(s) with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" appeared more than Once.', filename, err_barcode_multi, err_sku_multi) 
                raise JobError('Error while processing the File %s : Product(s) with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" appeared more than Once.' % (filename, err_barcode_multi, err_sku_multi))
        elif err_supplier:
            _logger.error('Error while processing the File %s : Supplier(s) With Reference(s) "%s" does not Exists.',filename, err_supplier) 
            raise JobError('Error while processing the File %s : Supplier(s) With Reference(s) "%s" does not Exists.' % (filename,err_supplier,))
        return False
                        

    def get_supplier_stock(self):
        cr = pooler.get_db(self.session.cr.dbname).cursor()
        FILENAME = r'^.*\.csv$'
        file_ids = self._search(FILENAME)
        file_obj = self.session.pool.get('bots.file')
        for file_id in file_ids:
            err_barcode = []
            err_sku = []
            err_barcode_multi = []
            err_sku_multi = []
            err_supplier = []
            prod_ids = {}
            vendor = False
            with file_to_process(self.session, file_id[0]) as f:
                file = file_obj.browse(cr, SUPERUSER_ID, file_id[0])
                with open(file.full_path, "rb") as csv_file:
                    reader = csv.DictReader(csv_file)
                    for row in reader:                      
                        products = self._check_product(cr, row['SUPPLIER_BARCODE'], row['SKU'])
                        if not products:
                            err_barcode.append(row['SUPPLIER_BARCODE'])
                            err_sku.append(row['SKU'])
                        elif len(products)>1:
                            err_barcode_multi.append(row['SUPPLIER_BARCODE'])
                            err_sku_multi.append(row['SKU'])
                        else:
                            prod_ids[products[0]] = row['QUANTITY']
                        if not vendor:
                            vendor = self._check_vendor(cr, row['SUPPLIER_ID'])
                        if not vendor and row['SUPPLIER_ID'] not in err_supplier: 
                            err_supplier.append(row['SUPPLIER_ID'])
                    self._raise_error(file_id[1],err_barcode, err_sku, err_supplier, err_barcode_multi, err_sku_multi)
                    vendor_obj = self.session.pool.get('res.partner').browse(self.session.cr, SUPERUSER_ID, vendor[0])
                    vendor_prod_ids = self.session.pool.get('product.product').search(self.session.cr, SUPERUSER_ID, [('seller_ids.name.id','=',vendor_obj.id)])
                    for prodid,qtyval in prod_ids.iteritems():
                        product_obj = self.session.pool.get('product.product').browse(self.session.cr, SUPERUSER_ID, prodid)
                    
                        qty = float(qtyval)
                        #% to Exclude Calculation
                        if vendor_obj.percent_to_exclude:
                            exclude_qty = (vendor_obj.percent_to_exclude/100.0) * qty
                            qty = qty - int(exclude_qty) 
                        product_obj.write({'supplier_stock_integration_qty':qty})
                    
                        vendor_prod_ids.remove(product_obj.id)
                    
                    #Out of Stock Products    
                    if vendor_prod_ids and vendor_obj.flag_skus_out_of_stock:
                        self.session.pool.get('product.product').write(self.session.cr, SUPERUSER_ID, vendor_prod_ids, {'supplier_stock_integration_qty':0})
        cr.commit()
        cr.close()        
        return True

@job
def import_supplier_stock(session, name, backend_id):
    env = get_environment(session, 'bots.product', backend_id)
    stock_importer = env.get_connector_unit(BotsStockImport)
    stock_importer.import_supplier_stock()
    return True
