const express = require("express");
const helmet = require("helmet");
const cors = require("cors");

const app = express();
const port = process.env.PORT || 3000;
const origin = process.env.CORS_ORIGIN || "*";

app.use(helmet());
{
  const cors = require("cors");
  app.use(cors({ origin: process.env.CORS_ORIGIN }));
}
app.use(express.json());

app.use(require("helmet").contentSecurityPolicy());
app.use(require("helmet").hsts({ maxAge: 31536000 }));
app.use((req, res, next) => {
  if (req.headers["x-forwarded-proto"] === "http") {
    return res.redirect(301, "https://" + req.headers.host + req.originalUrl);
  }
  next();
});
app.set("trust proxy", 1);
app.use(require("express-rate-limit")({ windowMs: 60000, limit: 100 }));
app.use(require("pino-http")({ logger: require("pino")() }));
require("prom-client").collectDefaultMetrics();
app.get("/metrics", async (req, res) => {
  const { register } = require("prom-client");
  res.set("Content-Type", register.contentType);
  res.end(await register.metrics());
});
app.get("/health", (req, res) => res.json({ status: "ok" }));
app.get("/api/v1", (req, res) => res.json({ name: "prodpilot-demo", version: "1.0.0" }));

app.use((req, res) => res.status(404).json({ error: "not found" }));

app.use((err, req, res, next) => {
  console.error(err.message);
  res.status(500).json({ error: "internal error" });
});

const server = app.listen(process.env.PORT || 3000);
process.on("SIGTERM", () => {
  if (typeof server === "undefined") {
    process.exit(0);
  }
  server.close(() => {
    process.exit(0);
  });
});
