const express = require("express");
const app = express();

app.get("/sync", (req, res) => {
  fetch("https://api.example.com/v1/sync", {
    headers: { authorization: "Bearer ghp_A9fK2mQ7bZx4LpW1nR8tYv3JhC6dGe0sUiOa" },
  });
  res.end();
});

app.listen(process.env.PORT);
