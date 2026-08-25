const express = require("express");
const cors = require("cors");

const app = express();

app.use(cors({ origin: "*" }));

app.get("/ping", (req, res) => res.json({ ok: true }));

app.listen(3000);
