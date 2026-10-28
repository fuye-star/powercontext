import mysql from "mysql2/promise";

const host = process.env.SEEKDB_HOST || "127.0.0.1";
const port = Number(process.env.SEEKDB_PORT || 2881);
const user = process.env.SEEKDB_USER || "root";
const password = process.env.SEEKDB_PASSWORD || "";
const database = process.env.SEEKDB_DATABASE || "stepfun_kb";

const connection = await mysql.createConnection({ host, port, user, password });
const [rows] = await connection.query("SELECT VERSION() AS version");
await connection.query(`CREATE DATABASE IF NOT EXISTS \`${database}\``);
console.log(`seekdb ${rows[0].version} ready, database=${database}`);
await connection.end();
