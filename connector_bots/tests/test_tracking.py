import openerp.tests.common as common


class TestTracking(common.TransactionCase):

    def setUp(self):
        super(TestTracking, self).setUp()

        picking_obj = self.registry('stock.picking.out')
        carrier_obj = self.registry('delivery.warehouse.carrier')
        self.tracking_obj = self.registry('stock.picking.carrier.tracking')

        picking_id1 = picking_obj.create({})
        picking_id2 = picking_obj.create({})

        carrier_id1 = carrier_obj.create(
            self.cr, self.uid, {'name': 'Trackers United', 'carrier_code': 'TU', 'tracking_link': 'https://www.trackersunited.com/ref=[[code]]'}
        )

        self.tracking_id1 = self.tracking_obj.create(
            self.cr, self.uid, {'picking_id': picking_id1, 'carrier_id': carrier_id1, 'tracking_reference': 'TU123456789'}
        )

        carrier_id2 = carrier_obj.create(
            self.cr, self.uid, {'name': 'Blank Label', 'carrier_code': 'BL', 'tracking_link': ''}
        )

        self.tracking_id2 = self.tracking_obj.create(
            self.cr, self.uid, {'picking_id': picking_id2, 'carrier_id': carrier_id2, 'tracking_reference': 'NOT_TRACKED'}
        )

    def test_odoo_link_with_reference(self):
        tracking = self.tracking_obj.browse(self.tracking_id1)

        self.assertEqual(
            tracking.tracking_link,
            '<a href="https://www.trackersunited.com/ref=TU123456789" target="_blank">Trackers United - TU123456789</a>'
        )

    def test_odoo_link_no_reference(self):
        tracking = self.tracking_obj.browse(self.tracking_id2)

        self.assertEqual(tracking.tracking_link, 'BL - NOT_TRACKED')

    def test_magento_link_with_reference(self):
        tracking = self.tracking_obj.browse(self.tracking_id1)

        self.assertEqual(tracking.magento_tracking_link, 'https://www.trackersunited.com/ref=TU123456789')

    def test_magento_link_no_reference(self):
        tracking = self.tracking_obj.browse(self.tracking_id2)

        self.assertEqual(tracking.magento_tracking_link, '')

