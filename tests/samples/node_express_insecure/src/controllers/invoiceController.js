const Invoice = require("../models/Invoice");

async function list(req, res) {
  const invoices = await Invoice.find({ paid: false });
  res.json({ invoices });
}

module.exports = { list };
