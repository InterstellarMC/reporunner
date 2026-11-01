import unittest
from app import greet

class GreetingTest(unittest.TestCase):
    def test_greeting(self):
        self.assertEqual(greet("team"), "Hello, team!")
