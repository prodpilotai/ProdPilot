const express = require("express");
const helmet = require("helmet");
const cors = require("cors");

const app = express();

app.use(cors({ origin: "https://admin.example.com" }));

app.get("/invoices", (req, res) => {
  res.json({ invoices: [] });
});

app.use((err, req, res, next) => {
  res.status(500).json({ error: err.stack });
});

app.get("/invoices/:id", (req, res) => {
  res.json({ id: req.params.id });
});

app.use(helmet());

app.listen(3000);
