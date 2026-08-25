const User = require("../models/User");

async function list() {
  return User.find({ active: true });
}

module.exports = { list };
