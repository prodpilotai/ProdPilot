const express = require("express");
const controller = require("../controllers/userController");

const router = express.Router();

router.get("/", controller.list);

module.exports = router;
