const config = require('./config.json');
exports.multiply = value => {
  if (typeof config.multiplier !== 'number') throw new TypeError('config.multiplier must be number');
  return value * config.multiplier;
};
