const test = require('node:test');
const assert = require('node:assert/strict');
const {multiply} = require('../app');
test('multiplier is applied', () => assert.equal(multiply(3),9));
