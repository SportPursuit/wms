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
import math
import logging
from datetime import datetime

from openerp import pooler, netsvc, SUPERUSER_ID
from openerp.addons.connector.queue.job import job
from openerp.addons.connector.exception import JobError
from openerp.addons.connector.unit.synchronizer import ImportSynchronizer

from openerp.addons.connector_bots.unit.backend_adapter import BotsCRUDAdapter, file_to_process
from openerp.addons.connector_bots.backend import bots
from openerp.addons.connector_bots.connector import get_environment, add_checkpoint

logger = logging.getLogger(__name__)


SUPPLIER_STOCK_FEED = 'Supplier Stock Feed'


class ProductDetails(object):

    def __init__(self):

        self.products = {}
        self.identifiers = {}
        self.duplicate_rows = set()
        self.missing_products = []
        self.too_many_products = []
        self.invalid_quantity_values = []

    @property
    def error_message(self):
        error_message = ''

        if self.too_many_products:
            error_message += """
            Multiple products found:
            {products}
            """.format(products='\n'.join(self.too_many_products))

        if self.duplicate_rows:
            error_message += """
            Duplicate row(s) found:
            {products}
            """.format(products='\n'.join(self.duplicate_rows))

        if self.invalid_quantity_values:
            error_message += """
            Invalid quantity value(s) found:
            {products}
            """.format(products='\n'.join(self.invalid_quantity_values))

        return error_message or None


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

        missing_products_obj = self.session.pool.get('supplier.feed.missing.products')

        bots_file_id = self.session.pool.get('bots.file').search(
            self.session.cr, SUPERUSER_ID, [('full_path', '=', filename)]
        )

        if bots_file_id and len(bots_file_id) == 1:

            with file_to_process(self.session, bots_file_id[0], raise_if_processed=True, filemode='rU') as csv_file:
                supplier, product_details = self._preprocess_rows(csv_file)

                inventory_id = self._create_physical_inventory(supplier, product_details)

                for sku, barcode, quantity in product_details.missing_products:

                    try:
                        missing_product = {
                            'filename': filename,
                            'inventory_id': inventory_id,
                            'supplier_id': supplier.id,
                            'product_sku': sku,
                            'product_barcode': barcode,
                            'quantity': quantity
                        }
                        missing_products_obj.create(self.session.cr, self.session.uid, missing_product)

                    except Exception:
                        logger.exception('Failed to add missing product. %s : %s %s' % (filename, sku, barcode))

        else:
            raise Exception('No bots_file entry found for file %s' % filename)

    def _preprocess_rows(self, csv_file):
        """ Do some pre-processing on the csv rows to make sure everything is as we expect. 
            Will raise a JobError if anything is not correct.
        """
        rows = [row for row in csv.DictReader(csv_file)]

        if not rows:
            raise Exception('File appears to be empty')

        product_details = self._get_product_details(rows)
        supplier, all_supplier_products, supplier_error_message = self._check_supplier_details(rows, product_details)

        products_error_message = product_details.error_message

        if products_error_message or supplier_error_message:
            raise JobError("""
                {supplier_errors}
                {product_errors}
            """.format(supplier_errors=supplier_error_message, product_errors=products_error_message))
        else:
            if supplier.flag_skus_out_of_stock:
                for product_id in all_supplier_products:
                    if product_id not in product_details.products:
                        product_details.products[product_id] = 0

            return supplier, product_details

    def _get_product_details(self, rows):
        """ Ensure that the product information in the csv is correct
        """

        processed_rows = set()
        product_details = ProductDetails()

        for row in rows:

            barcode = row['SUPPLIER_BARCODE'].strip()
            sku = row['SKU'].strip()
            qty = row['QUANTITY']

            identifier = '%s %s' % (sku, barcode)

            if identifier in processed_rows:
                product_details.duplicate_rows.add(identifier)
                continue
            else:
                processed_rows.add(identifier)

            product_ids = self.session.pool.get('product.product').search(
                self.session.cr, SUPERUSER_ID, [('magento_barcode', '=', barcode), ('magento_supplier_sku', '=', sku)]
            )

            if len(product_ids) == 1:
                try:
                    qty = int(qty)

                    product_id = product_ids[0]

                    product_details.identifiers[product_id] = identifier

                    if qty >= 0:
                        product_details.products[product_id] = qty
                    else:
                        product_details.invalid_quantity_values.append('%s %s' % (identifier, qty))

                except ValueError:
                    product_details.invalid_quantity_values.append('%s %s' % (identifier, qty))

            elif len(product_ids) > 1:
                product_details.too_many_products.append(identifier)

            else:
                product_details.missing_products.append((sku, barcode, qty))

        return product_details

    def _check_supplier_details(self, rows, product_details):
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
                        extra_products = self._get_extra_products(supplier, product_details, all_supplier_products)
                        if extra_products:
                            extra_products = '\n'.join(
                                [product_details.identifiers[product.id] for product in extra_products])
                            error_message += """
                            Products that do not belong to the supplier:
                            {products}
                            """.format(products=extra_products)

        return supplier, all_supplier_products, error_message

    def _get_extra_products(self, supplier, product_details, all_supplier_products):
        """Return extra products that do not belong to the supplier
        or set feed quantity to zero if match rules"""
        incorrect_products = []  # Products that don't match rules below
        # products from odoo db
        # products from csv that don't match products from odoo db
        extra_products = list(set(product_details.products.keys()).difference(all_supplier_products))
        extra_products = self.session.pool.get('product.product').browse(
            self.session.cr, SUPERUSER_ID, extra_products
        )
        for extra_product in extra_products:
            for seller_id in extra_product.seller_ids:
                res_partner = seller_id.name
                if res_partner.id != supplier.id:
                    # The Supplier ID in the feed does not match the Supplier ID of the product in Odoo
                    if res_partner.id == res_partner.parent_id or res_partner.parent_id == supplier.parent_id:
                        # The Supplier ID in the feed is the parent of the Supplier ID of the product in Odoo OR
                        # The Supplier ID in the feed shares the parent of the Supplier ID of the product in Odoo
                        # The SKU's feed quantity should be set to zero
                        product_details.products[extra_product] = 0
                    else:
                        incorrect_products.append(extra_product)
        return incorrect_products if incorrect_products else []

    def get_supplier_stock(self, backend_id):

        csv_regex = r'^.*\.csv$'
        file_ids = self._search(csv_regex)

        job_obj = self.session.pool.get('queue.job')

        for _, filename in file_ids:

            # We want to be sure that we only spawn one active job for each file
            # Filtering by state != done will let us re-run an archived file if required
            # NOTE: Potential performance impact - might need to refactor into an sql query later on
            query = [
                ('model_name', '=', 'bots.backend.supplier.feed'),
                ('func_string', 'ilike', '%{filename}%'.format(filename=filename)),
                ('state', '!=', 'done')
            ]

            if not job_obj.search(self.session.cr, self.session.uid, query):

                process_supplier_stock_file.delay(
                    self.session, 'bots.backend.supplier.feed', backend_id, filename, priority=5
                )

        return True

    # FIXME
    # This is copy-pasted and modified from sp_backorder_limt SportPursuitBackorderProductImport in the interest
    # of getting this released sooner rather than later.

    def _create_physical_inventory(self, supplier, product_details):
        stock_location_id = self.session.pool.get('stock.location').search(
            self.session.cr, SUPERUSER_ID, [('name', '=', SUPPLIER_STOCK_FEED)]
        )[0]

        today = datetime.strftime(datetime.now(), "%d-%m-%Y")

        inventory_record = {
            'state': 'draft',
            'name': 'Stock Integration Update - %s : %s' % (supplier.name, today)
        }

        inventory_id = self.session.create('stock.inventory', inventory_record)

        for product_id, qty in product_details.products.iteritems():

            # % to Exclude Calculation
            if qty > 0 and supplier.percent_to_exclude > 0:
                # The 'or 1' is for the edge-case where the qty is 1 which will leave an exclude quantity of 0 after
                # floor()
                exclude_qty = math.ceil((supplier.percent_to_exclude / 100.0) * qty) or 1
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

        return inventory_id


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
