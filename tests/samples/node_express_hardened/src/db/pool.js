const { Pool } = require("pg");

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  user: process.env.DB_USER,
  password: process.env.DB_PASSWORD,
  max: 10,
});

module.exports = pool;
