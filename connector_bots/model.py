from openerp.osv import orm

import logging
logger = logging.getLogger(__name__)


REQUEUE_ERRORS = [
    "waits for ShareLock on transaction",
    "'NoneType' object has no attribute 'pop'",
    "could not serialize access due to concurrent update"
]


class QueueJob(orm.Model):
    _inherit = 'queue.job'

    def requeue_failed_picking_confs(self, cr, uid, context=None):
        requeue_ids = []
        failed_conf_ids = self.search(cr, uid, [('name', '=', 'Function import_picking_file'), ('state', '=', 'failed')])
        logger.info("The following failed picking conf file imports were found: %s", failed_conf_ids)
        failed_confs = self.browse(cr, uid, failed_conf_ids)
        for failed_conf in failed_confs:
            for message in REQUEUE_ERRORS:
                try:
                    exc_true = message in failed_conf.exc_info
                except TypeError:
                    exc_true = False
                try:
                    result_true = message in failed_conf.result
                except TypeError:
                    result_true = False
                if exc_true or result_true:
                    requeue_ids.append(failed_conf.id)
                    break
        if requeue_ids:
            requeue_ids = list(set(requeue_ids))
            logger.info("Requeuing picking file imports wth ids %s", requeue_ids)
            self.requeue(cr, uid, requeue_ids, context=context)
