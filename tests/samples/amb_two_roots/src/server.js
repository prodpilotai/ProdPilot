const express = require("express");
const app = express();

app.use("/orders", require("./routes/orders"));
app.use("/billing", require("./routes/billing"));

app.listen(process.env.PORT);
