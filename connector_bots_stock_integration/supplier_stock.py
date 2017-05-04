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

    def import_supplier_stock(self, new_cr=True):
        """
        Import the picking confirmation from Bots
        """
        self.backend_adapter.get_supplier_stock(new_cr=new_cr)

@bots
class StockAdapter(BotsCRUDAdapter):
    _model_name = 'bots.product'
    
    def _read_csv(self, record_file):
        """ Returns a CSV-parsed iterator of all empty lines in the file

        :throws csv.Error: if an error is detected during CSV parsing
        :throws UnicodeDecodeError: if ``options.encoding`` is incorrect
        """
        cr = pooler.get_db(self.session.cr.dbname).cursor()
        file_obj = self.session.pool.get('bots.file')
        file = file_obj.browse(cr, SUPERUSER_ID, record_file)
        fd = open(file.full_path, "rb")
        csv_iterator = csv.reader(
            fd,
            quotechar=str('"'),
            delimiter=str(','))
        csv_nonempty = itertools.ifilter(None, csv_iterator)
        encoding = 'utf-8'
        ret = itertools.imap(
            lambda row: [item.decode(encoding) for item in row],
            csv_nonempty)
        cr.commit()
        cr.close()
        return ret
        
    def _check_product(self, barcode=False, sku=False):
        cr = pooler.get_db(self.session.cr.dbname).cursor()
        if barcode and sku:
            prodids = self.session.pool.get('product.product').search(cr, SUPERUSER_ID, [('magento_barcode','=',barcode), ('magento_supplier_sku','=',sku)])
            if prodids:
                return prodids[0] 
        return False
        
    def _check_vendor(self, supplier_id=False):
        cr = pooler.get_db(self.session.cr.dbname).cursor()
        if supplier_id:
            suppids = self.session.pool.get('res.partner').search(cr, SUPERUSER_ID, [('ref','=',supplier_id), ('supplier','=',True)])
            if suppids:
                return suppids[0]
        return False
        
    def _raise_error(self, filename=False, err_barcode=[], err_sku=[],err_supplier=[]):
        if err_barcode or err_sku:
            if err_supplier:
                _logger.info('Errors while processing the File %s : Product(s) with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" does not Exists and Supplier(s) With Reference(s) "%s" does not Exists.', filename, err_barcode, err_sku, err_supplier) 
                raise JobError('Errors while processing the File %s : Product with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" does not Exists and Supplier(s) With Reference(s) "%s" does not Exists.' % (filename, err_barcode,err_sku, err_supplier,))
            else:
                _logger.info('Errors while processing the File %s : Product(s) with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" does not Exists.',filename, err_barcode, err_sku) 
                raise JobError('Errors while processing the File %s : Product(s) with Magento Barcode(s) "%s" or with Magento Supplier SKU(s) "%s" does not Exists.' % (filename,err_barcode, err_sku,))                    
        if err_supplier:
            _logger.info('Errors while processing the File %s : Supplier(s) With Reference(s) "%s" does not Exists.',filename, err_supplier) 
            raise JobError('Errors while processing the File %s : Supplier(s) With Reference(s) "%s" does not Exists.' % (filename,err_supplier,))
        return False
                        

    def get_supplier_stock(self, new_cr=True):
        cr = pooler.get_db(self.session.cr.dbname).cursor()
        res = []
        FILENAME = r'^.*\.csv$'
        file_ids = self._search(FILENAME)
        for file_id in file_ids:
            err_barcode = []
            err_sku = []
            err_supplier = []
            prod_ids = []
            vendor = False
            with file_to_process(self.session, file_id[0], new_cr=new_cr) as f:
                rows_to_import = self._read_csv(file_id[0])
                data = [
                    row for row in rows_to_import
                    if any(row)
                ]
                for cnt in range(1,len(data)):
                    rec = data[cnt]
                    product = self._check_product(rec[1], rec[0])
                    if not product:
                        err_barcode.append(rec[1])
                        err_sku.append(rec[0])
                    if not vendor:
                        vendor = self._check_vendor(rec[3])
                    if not vendor: 
                        err_supplier.append(rec[3])
                self._raise_error(file_id[1],err_barcode, err_sku, set(err_supplier))
                if vendor:
                    if not isinstance(vendor, list):
                        vendor = [vendor]
                    vendor_obj = self.session.pool.get('res.partner').browse(self.session.cr, SUPERUSER_ID, vendor[0])
                    vendor_prod_ids = self.session.pool.get('product.product').search(self.session.cr, SUPERUSER_ID, [('seller_ids.name.id','=',vendor_obj.id)])
                    
                for cnt in range(1,len(data)):
                    rec = data[cnt]
                    product = self._check_product(rec[1], rec[0])
                    vendor = self._check_vendor(rec[3])
                    if not isinstance(product, list):
                        product = [product]
                    if not isinstance(vendor, list):
                        vendor = [vendor]
                    product_obj = self.session.pool.get('product.product').browse(self.session.cr, SUPERUSER_ID, product[0])
                    
                    qty = float(rec[2])
                    #% to Exclude Calculation
                    if vendor_obj.percent_to_exclude:
                        exclude_qty = (vendor_obj.percent_to_exclude/float(100))*qty
                        qty = qty - exclude_qty
                        qty = int(round(qty))                    
                    product_obj.write({'supplier_stock_integration_qty':qty})
                    
                    vendor_prod_ids.remove(product_obj.id)
                    
                #Out of Stock Products    
                if vendor_prod_ids and vendor_obj.flg_sku_out_of_stock:
                    for productobj in self.session.pool.get('product.product').browse(self.session.cr, SUPERUSER_ID, vendor_prod_ids):
                        productobj.write({'supplier_stock_integration_qty':0})
                
        return res

@job
def import_supplier_stock(session, name, backend_id, new_cr=True):
    env = get_environment(session, 'bots.product', backend_id)
    stock_importer = env.get_connector_unit(BotsStockImport)
    stock_importer.import_supplier_stock(new_cr=new_cr)
    return True


    


    
