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

from openerp import pooler, netsvc, SUPERUSER_ID
from openerp.addons.connector.queue.job import job
from openerp.addons.connector.exception import JobError
from openerp.addons.connector.unit.synchronizer import ImportSynchronizer

from openerp.addons.connector_bots.unit.backend_adapter import BotsCRUDAdapter, file_to_process
from openerp.addons.connector_bots.backend import bots
from openerp.addons.connector_bots.connector import get_environment, add_checkpoint

logger = logging.getLogger(__name__)


@bots
class BotsStockImport(ImportSynchronizer):
    _model_name = ['bots.backend.supplier.feed']

    def import_supplier_stock(self):
        """ Creates entries in the bots.file table and spawns a job per entry to process it
        """
        self.backend_adapter.get_supplier_stock()

    def process_supplier_stock_file(self, filename):
        """ Processes the supplier stock feed csv
        """
        self.backend_adapter.process_stock_feed(filename)

@bots
class StockAdapter(BotsCRUDAdapter):
    _model_name = 'bots.backend.supplier.feed'

    def process_stock_file(self, filename):

        with file_to_process(self.session, filename) as csv_file:
            supplier, product_updates, all_supplier_products = self._preprocess_rows(csv_file)

            for product_id, qty in product_updates:
                product_obj = self.session.pool.get('product.product').browse(self.session.cr, SUPERUSER_ID, product_id)

                # % to Exclude Calculation
                if supplier.percent_to_exclude:
                    exclude_qty = (supplier.percent_to_exclude / 100.0) * qty
                    qty = qty - int(exclude_qty)

                product_obj.write({'supplier_stock_integration_qty': qty})

                all_supplier_products.remove(product_obj.id)

            # Out of Stock Products
            if all_supplier_products and supplier.flag_skus_out_of_stock:
                self.session.pool.get('product.product').write(
                    self.session.cr, SUPERUSER_ID, all_supplier_products, {'supplier_stock_integration_qty': 0}
                )

    def _preprocess_rows(self, csv_file):
        """ Do some pre-processing on the csv rows to make sure everything is as we expect. 
            Will raise a JobError if anything is not correct.
        """
        rows = [row for row in csv.DictReader(csv_file)]

        product_upates, products_error_message = self._check_products(rows)
        supplier, all_supplier_products, supplier_error_message = self._check_supplier(rows, product_upates)

        if products_error_message or supplier_error_message:
            raise JobError("""
                {supplier_errors}
                {product_errors}
            """.format(supplier_errors=supplier_error_message, product_errors=products_error_message))
        else:
            return supplier, product_upates, all_supplier_products

    def _check_products(self, rows):
        """ Ensure that the product information in the csv is correct
        """

        products = []
        missing_products = []
        too_many_products = []
        duplicate_rows = set()
        processed_rows = set()
        invalid_quantity_values = []

        for row in rows:

            barcode = row['SUPPLIER_BARCODE']
            sku = row['SKU']
            qty = row['QUANTITY']

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
                try:
                    qty = int(qty)
                    if qty < 1:
                        raise ValueError()
                except ValueError:
                    invalid_quantity_values.append('%s %s' % (identifier, qty))
                else:
                    products.append((product_ids[0], qty))

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
                    self.session.cr, SUPERUSER_ID, [('ref', '=', supplier_id), ('supplier','=',True)]
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
                    supplier = self.session.pool.get('res.partner').browse(self.session.cr, SUPERUSER_ID, partner_ids[0])
                    all_supplier_products = self.session.pool.get('product.product').search(
                        self.session.cr, SUPERUSER_ID, [('seller_ids.name.id', '=', supplier.id)]
                    )

                    if supplier:
                        extra_products = set({product[0] for product in product_updates}) - set(all_supplier_products)
                        if extra_products:
                            error_message += """
                            Products that do not belong to the supplier:
                            {products}
                            """.format(products='\n'.join(extra_products))

        return supplier, all_supplier_products, error_message
                        
    def get_supplier_stock(self):

        FILENAME = r'^.*\.csv$'
        file_ids = self._search(FILENAME)

        for _, filename in file_ids:
            process_supplier_stock_file.delay(
                self.session, 'bots.backend.supplier.feed', self.bots.id, filename, priority=5
            )

        return True

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
