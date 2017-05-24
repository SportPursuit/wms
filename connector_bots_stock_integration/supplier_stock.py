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
import logging
from datetime import datetime

from openerp.osv import orm
from openerp import pooler, netsvc, SUPERUSER_ID
from openerp.addons.connector.queue.job import job
from openerp.addons.connector.exception import JobError
from openerp.addons.connector.unit.synchronizer import ImportSynchronizer

from openerp.addons.connector_bots.unit.backend_adapter import BotsCRUDAdapter, file_to_process
from openerp.addons.connector_bots.backend import bots
from openerp.addons.connector_bots.connector import get_environment, add_checkpoint

logger = logging.getLogger(__name__)


SUPPLIER_STOCK_FEED = 'Supplier Stock Feed'


@bots
class BotsStockImport(ImportSynchronizer):
    _model_name = ['bots.backend.supplier.feed']

    def import_supplier_stock(self):
        """ Creates entries in the bots.file table and spawns a job per entry to process it
        """
        self.backend_adapter.get_supplier_stock(self.environment.backend_record.id)

    def process_supplier_stock_file(self, filename):
        """ Processes the supplier stock feed csv
        """
        self.backend_adapter.process_stock_file(filename)


@bots
class StockAdapter(BotsCRUDAdapter):
    _model_name = 'bots.backend.supplier.feed'

    def process_stock_file(self, filename):

        bots_file_id = self.session.pool.get('bots.file').search(
            self.session.cr, SUPERUSER_ID, [('full_path', '=', filename)]
        )

        if bots_file_id and len(bots_file_id) == 1:

            with file_to_process(self.session, bots_file_id[0], raise_if_processed=True) as csv_file:
                supplier, product_updates = self._preprocess_rows(csv_file)

                self._create_physical_inventory(supplier, product_updates)

        else:
            raise Exception('No bots_file entry found for file %s' % filename)

    def _preprocess_rows(self, csv_file):
        """ Do some pre-processing on the csv rows to make sure everything is as we expect. 
            Will raise a JobError if anything is not correct.
        """
        rows = [row for row in csv.DictReader(csv_file)]

        if not rows:
            raise Exception('File appears to be empty')

        product_updates, products_error_message = self._check_products(rows)
        supplier, all_supplier_products, supplier_error_message = self._check_supplier(rows, product_updates)

        if products_error_message or supplier_error_message:
            raise JobError("""
                {supplier_errors}
                {product_errors}
            """.format(supplier_errors=supplier_error_message, product_errors=products_error_message))
        else:
            if supplier.flag_skus_out_of_stock:
                for product_id in all_supplier_products:
                    if product_id not in product_updates:
                        product_updates[product_id] = 0

            return supplier, product_updates

    def _check_products(self, rows):
        """ Ensure that the product information in the csv is correct
        """

        products = {}
        missing_products = []
        too_many_products = []
        duplicate_rows = set()
        processed_rows = set()
        invalid_quantity_values = []

        for row in rows:

            barcode = row['SUPPLIER_BARCODE']
            sku = row['SKU']
            qty = int(row['QUANTITY'])

            identifier = '%s %s' % (sku, barcode)

            if identifier in processed_rows:
                duplicate_rows.add(identifier)
                continue
            else:
                processed_rows.add(identifier)

            product_ids = self.session.pool.get('product.product').search(
                self.session.cr, SUPERUSER_ID, [('magento_barcode', '=', barcode), ('magento_supplier_sku', '=', sku)]
            )

            if len(product_ids) == 1:
                if qty >= 0:
                    products[product_ids[0]] = qty
                else:
                    invalid_quantity_values.append('%s %s' % (identifier, qty))

            elif len(product_ids) > 1:
                too_many_products.append(identifier)

            else:
                missing_products.append(identifier)

        error_message = ''

        if missing_products:
            error_message += """
            Missing product(s):
            {products}
            """.format(products='\n'.join(missing_products))

        if too_many_products:
            error_message += """
            Multiple products found:
            {products}
            """.format(products='\n'.join(missing_products))

        if duplicate_rows:
            error_message += """
            Duplicate row(s) found:
            {products}
            """.format(products='\n'.join(duplicate_rows))

        if invalid_quantity_values:
            error_message += """
            Invalid quantity value(s) found:
            {products}
            """.format(products='\n'.join(invalid_quantity_values))

        return products, error_message

    def _check_supplier(self, rows, product_updates):
        """ Ensure that the supplier information in the csv is correct
        """

        supplier_ids = list({row['SUPPLIER_ID'] for row in rows})
        error_message = ''
        supplier = None
        all_supplier_products = []

        if len(supplier_ids) > 1:
            error_message = """
            More than one supplier id found in file: 
            {ids}
            """.format(ids='\n'.join(supplier_ids))

        else:
            supplier_id = supplier_ids[0]

            if supplier_id is None:
                error_message = """
                Supplier id field is empty
                """

            else:

                partner_ids = self.session.pool.get('res.partner').search(
                    self.session.cr, SUPERUSER_ID, [('ref', '=', supplier_id), ('supplier', '=', True)]
                )

                if len(partner_ids) == 0:
                    error_message = """
                    No supplier found for id {id}
                    """.format(id=supplier_id)
                elif len(partner_ids) > 1:
                    error_message = """
                    Multiple suppliers found for id {id}
                    """.format(id=supplier_id)
                else:
                    supplier = self.session.pool.get('res.partner').browse(
                        self.session.cr, SUPERUSER_ID, partner_ids[0]
                    )
                    all_supplier_products = self.session.pool.get('product.product').search(
                        self.session.cr, SUPERUSER_ID, [('seller_ids.name.id', '=', supplier.id)]
                    )

                    if supplier:
                        extra_products = set(product_updates.keys()) - set(all_supplier_products)
                        if extra_products:
                            error_message += """
                            Products that do not belong to the supplier:
                            {products}
                            """.format(products='\n'.join(extra_products))

        return supplier, all_supplier_products, error_message
                        
    def get_supplier_stock(self, backend_id):

        csv_regex = r'^.*\.csv$'
        file_ids = self._search(csv_regex)

        for _, filename in file_ids:
            process_supplier_stock_file.delay(
                self.session, 'bots.backend.supplier.feed', backend_id, filename, priority=5
            )

        return True

    # FIXME
    # This is copy-pasted and modified from sp_backorder_limt SportPursuitBackorderProductImport in the interest
    # of getting this released sooner rather than later.

    def _create_physical_inventory(self, supplier, product_updates):
        stock_location_id = self.session.pool.get('stock.location').search(
            self.session.cr, SUPERUSER_ID, [('name', '=', SUPPLIER_STOCK_FEED)]
        )[0]

        today = datetime.strftime(datetime.now(), "%d-%m-%Y")

        inventory_record = {
            'state': 'draft',
            'name': 'Stock Integration Update - %s : %s' % (supplier.name, today)
        }

        inventory_id = self.session.create('stock.inventory', inventory_record)

        for product_id, qty in product_updates.iteritems():

            # % to Exclude Calculation
            if supplier.percent_to_exclude:
                exclude_qty = (supplier.percent_to_exclude / 100.0) * qty
                qty = qty - int(exclude_qty)

            inventory_line_record = {
                'product_uom': 1,
                'product_id': product_id,
                'location_id': stock_location_id,
                'inventory_id': inventory_id,
                'product_qty': qty
            }

            self.session.create('stock.inventory.line', inventory_line_record)

        inventory_obj = self.session.pool['stock.inventory']
        inventory_obj.action_confirm(self.session.cr, SUPERUSER_ID, [inventory_id], self.session.context)
        inventory_obj.action_done(self.session.cr, SUPERUSER_ID, [inventory_id], self.session.context)


@job
def import_supplier_stock(session, name, backend_id):
    env = get_environment(session, name, backend_id)
    stock_importer = env.get_connector_unit(BotsStockImport)
    stock_importer.import_supplier_stock()
    return True


@job
def process_supplier_stock_file(session, name, backend_id, filename):
    env = get_environment(session, name, backend_id)
    stock_importer = env.get_connector_unit(BotsStockImport)
    stock_importer.process_supplier_stock_file(filename)
    return True
