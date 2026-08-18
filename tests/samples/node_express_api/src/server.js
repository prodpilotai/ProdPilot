const express = require("express");
const app = express();

app.get("/orders", (req, res) => {
  res.json({ orders: [] });
});

app.listen(3000);
