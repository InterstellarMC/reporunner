import unittest
from app import check
class CapabilityTest(unittest.TestCase):
    def test_required_capability(self):
        self.assertTrue(check())
