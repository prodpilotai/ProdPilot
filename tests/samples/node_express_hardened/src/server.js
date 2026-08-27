const express = require("express");
const helmet = require("helmet");
const cors = require("cors");
const rateLimit = require("express-rate-limit");
const client = require("prom-client");
const logger = require("./logger");
const orderRoutes = require("./routes/orderRoutes");

const app = express();

app.use(helmet());
app.use(cors({ origin: process.env.CORS_ORIGIN }));
app.use(rateLimit({ windowMs: 60000, max: 100 }));

app.use((req, res, next) => {
  if (req.headers["x-forwarded-proto"] === "http") {
    return res.redirect("https://" + req.headers.host + req.url);
  }
  next();
});

app.get("/health", (req, res) => res.json({ status: "ok" }));

app.get("/metrics", async (req, res) => {
  res.set("Content-Type", client.register.contentType);
  res.end(await client.register.metrics());
});

app.use("/api/v1/orders", orderRoutes);

app.use((err, req, res, next) => {
  logger.error("request failed", { path: req.path });
  res.status(500).json({ error: "request failed" });
});

const server = app.listen(process.env.PORT);

process.on("SIGTERM", () => {
  logger.info("draining connections");
  server.close(() => process.exit(0));
});
