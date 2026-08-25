const userService = require("../services/userService");

async function list(req, res, next) {
  try {
    const users = await userService.list();
    res.json({ users });
  } catch (err) {
    next(err);
  }
}

module.exports = { list };
