const express = require("express");
const { Client } = require("pg");

const app = express();
const client = new Client({ connectionString: process.env.DATABASE_URL });

app.get("/rows", async (req, res) => res.json(await client.query("SELECT 1")));

app.listen(process.env.PORT);
