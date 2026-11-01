import unittest
from app import multiply
class MultiplyTest(unittest.TestCase):
    def test_multiply(self):
        self.assertEqual(multiply(3), 9)
