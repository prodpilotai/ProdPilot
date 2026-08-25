const express = require("express");
const Invoice = require("../models/Invoice");

const router = express.Router();

router.get("/", async (req, res) => {
  const invoices = await Invoice.find({ paid: false });
  res.json({ invoices });
});

module.exports = router;
