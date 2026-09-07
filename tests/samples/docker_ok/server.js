const http = require("http");

const port = process.env.PORT || 3000;

http
  .createServer((req, res) => {
    if (req.url === "/health") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "ok" }));
      return;
    }
    res.writeHead(404);
    res.end();
  })
  .listen(port, () => console.log(`listening on ${port}`));
