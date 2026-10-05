import unittest
from talkmore.delivery import TextDelivery


class DeliveryTests(unittest.TestCase):
    def setup_delivery(self, held=False):
        self.events = []
        self.queue = []
        self.status = []
        self.now = 0
        self.held = held
        return TextDelivery(lambda text: self.events.append(('copy', text)),
                            lambda: self.held, lambda: self.events.append(('paste',)),
                            lambda delay, fn: self.queue.append(fn), lambda: self.now)

    def test_copies_before_single_paste_without_target_metadata(self):
        delivery = self.setup_delivery()
        delivery.deliver('你好', self.status.append)
        self.assertEqual(self.events, [('copy', '你好'), ('paste',)])
        self.assertEqual(len(self.status), 1)

    def test_waits_for_release_and_pastes_at_most_once(self):
        delivery = self.setup_delivery(held=True)
        delivery.deliver('你好', self.status.append)
        self.assertEqual(self.events, [('copy', '你好')])
        callback = self.queue.pop()
        self.held = False
        callback()
        callback()
        self.assertEqual(self.events, [('copy', '你好'), ('paste',)])

    def test_timeout_or_cancel_does_not_paste(self):
        for cancel in (False, True):
            delivery = self.setup_delivery(held=True)
            delivery.deliver('你好', self.status.append)
            if cancel:
                delivery.cancel()
            self.now = 3
            self.queue.pop()()
            self.assertEqual(self.events, [('copy', '你好')])

    def test_new_delivery_invalidates_previous_pending_paste(self):
        delivery = self.setup_delivery(held=True)
        delivery.deliver('旧文字', self.status.append)
        old_callback = self.queue.pop()
        delivery.deliver('新文字', self.status.append)
        new_callback = self.queue.pop()
        self.held = False
        old_callback()
        new_callback()
        self.assertEqual(self.events, [('copy', '旧文字'), ('copy', '新文字'), ('paste',)])
        self.assertEqual(len(self.status), 1)

    def test_copy_failure_never_pastes(self):
        delivery = self.setup_delivery()
        def fail(text):
            raise RuntimeError('unavailable')
        delivery.copy = fail
        delivery.deliver('你好', self.status.append)
        self.assertEqual(self.events, [])
        self.assertIn('无法复制', self.status[0])

    def test_paste_failure_not_retried(self):
        delivery = self.setup_delivery()
        def fail():
            self.events.append(('paste',))
            raise RuntimeError('unavailable')
        delivery.paste = fail
        delivery.deliver('你好', self.status.append)
        self.assertEqual(self.events, [('copy', '你好'), ('paste',)])
        self.assertEqual(self.queue, [])
