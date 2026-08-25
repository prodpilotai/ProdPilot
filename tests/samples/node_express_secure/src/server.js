const express = require("express");
const helmet = require("helmet");
const cors = require("cors");
const userRoutes = require("./routes/userRoutes");

const app = express();

app.use(helmet());
app.use(cors({ origin: process.env.CORS_ORIGIN }));
app.use(express.json());

app.get("/health", (req, res) => {
  res.json({ status: "ok" });
});

app.use("/api/v1/users", userRoutes);

app.use((err, req, res, next) => {
  res.status(err.status || 500).json({ error: "request failed" });
});

app.listen(process.env.PORT);
