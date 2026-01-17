const mysql = require('mysql2');

const db = mysql.createConnection({
    host: 'localhost',
    user: 'root',
    password: 'Xiaobai0409',
    database: 'telebot_fyp',
});

module.exports = db;
