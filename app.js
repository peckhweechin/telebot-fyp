// app.js
const express = require('express');
const mysql = require('mysql2');
const bcrypt = require('bcrypt');
const session = require('express-session');
const multer = require('multer');
const path = require('path');
const fs = require('fs');

const app = express();

const cloudinary = require("cloudinary").v2;

cloudinary.config({
  cloud_name: process.env.CLOUDINARY_CLOUD_NAME,
  api_key: process.env.CLOUDINARY_API_KEY,
  api_secret: process.env.CLOUDINARY_API_SECRET,
});

/* ================= DATABASE ================= */
require('dotenv').config();

const db = mysql.createPool({
  host: process.env.MYSQLHOST,
  user: process.env.MYSQLUSER,
  password: process.env.MYSQLPASSWORD,
  database: process.env.MYSQL_DATABASE,
  port: process.env.MYSQLPORT,
  waitForConnections: true,
  connectionLimit: 10,
  queueLimit: 0,
});

console.log('✅ MySQL Pool created');


/* ================= MIDDLEWARE ================= */
app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(express.static('public'));
app.use('/uploads', express.static(path.join(__dirname, 'public/uploads')));
app.use(express.static(path.join(__dirname, 'public')));
app.use('/images', express.static(path.join(__dirname, 'public/images')));

app.set('view engine', 'ejs');

app.use(
  session({
    secret: 'telebot_secret',
    resave: false,
    saveUninitialized: false,
  })
);

// Make admin available in all EJS views
app.use((req, res, next) => {
  res.locals.admin = req.session.admin || null;
  next();
});

/* ================= AUTH GUARD ================= */
function ensureAdmin(req, res, next) {
  if (req.session.admin) return next();
  res.redirect('/login');
}

/* ================= ROOT ================= */
app.get('/', (req, res) => {
  if (req.session.admin) return res.redirect('/admin/homepage');
  res.redirect('/login');
});

/* ================= AUTH ================= */
app.get('/login', (req, res) => {
  if (req.session.admin) return res.redirect('/admin/homepage');
  res.render('login');
});

app.post('/login', async (req, res) => {
  const { email, password } = req.body;

  db.query(
    'SELECT * FROM admin_users WHERE email = ? AND is_active = 1',
    [email],
    async (err, results) => {
      if (err || !results || results.length === 0) {
        return res.render('login', { error: 'Invalid credentials' });
      }

      const admin = results[0];
      const match = await bcrypt.compare(password, admin.password_hash);

      if (!match) {
        return res.render('login', { error: 'Invalid credentials' });
      }

      req.session.admin = admin;
      res.redirect('/admin/homepage');
    }
  );
});


app.get('/signup', (req, res) => {
  if (req.session.admin) return res.redirect('/admin/homepage');
  res.render('signup');
});

app.post('/signup', async (req, res) => {
  const { username, email, password, confirmPassword } = req.body;

  if (!username || !email || !password || !confirmPassword) {
    return res.render('signup', { error: 'All fields are required' });
  }

  if (password !== confirmPassword) {
    return res.render('signup', { error: 'Passwords do not match' });
  }

  const hash = await bcrypt.hash(password, 10);

  // 1️⃣ Check if admin already exists
  db.query(
    'SELECT id, is_active FROM admin_users WHERE email = ?',
    [email],
    (err, rows) => {
      if (err) {
        console.error(err);
        return res.render('signup', { error: 'Database error' });
      }

      // 2️⃣ Admin exists
      if (rows.length > 0) {
        const admin = rows[0];

        // 🔁 Reactivate if inactive
        if (admin.is_active === 0) {
          return db.query(
            'UPDATE admin_users SET name=?, password_hash=?, is_active=1 WHERE email=?',
            [username, hash, email],
            err2 => {
              if (err2) {
                console.error(err2);
                return res.render('signup', { error: 'Database error' });
              }
              return res.redirect('/login');
            }
          );
        }

        // 🚫 Already active
        return res.render('signup', {
          error: 'Admin already exists',
        });
      }

      // 3️⃣ Insert new admin
      db.query(
        'INSERT INTO admin_users (name, email, password_hash, is_active) VALUES (?, ?, ?, 1)',
        [username, email, hash],
        err3 => {
          if (err3) {
            console.error(err3);
            return res.render('signup', { error: 'Database error' });
          }
          res.redirect('/login');
        }
      );
    }
  );
});

app.post('/logout', (req, res) => {
  req.session.destroy(() => res.redirect('/login'));
});

/* ================= ADMIN PAGES ================= */
app.get('/admin/homepage', ensureAdmin, (req, res) => {
  res.render('admin_homepage');
});

app.get('/admin/users', ensureAdmin, (req, res) => {
  db.query(
    'SELECT * FROM users WHERE is_active = 1 ORDER BY created_at DESC',
    (err, users) => {
      if (err) return res.send('Failed to load users');
      res.render('users', { users });
    }
  );
});

app.get('/admin/orders', ensureAdmin, (req, res) => {
  db.query('SELECT * FROM orders ORDER BY created_at DESC', (err, orders) => {
    if (err) return res.send('Failed to load orders');
    res.render('adminOrders', { orders });
  });
});

/* ================= DASHBOARD APIs ================= */

/* ===== Helper: Safe Query Wrapper ===== */
const runQuery = (res, sql, defaultValue) => {
  db.query(sql, (err, rows) => {
    if (err) {
      console.error('Dashboard API Error:', err);
      return res.json(defaultValue);
    }
    res.json(rows);
  });
};


/* ================= SUMMARY ================= */
app.get('/api/dashboard/summary', ensureAdmin, (req, res) => {

  const sql = `
    SELECT
      (SELECT COUNT(*) FROM orders) AS totalOrders,

      (SELECT ROUND(IFNULL(SUM(total_cents),0)/100,2)
       FROM orders
       WHERE status IN ('paid','completed')
      ) AS totalRevenue,

      (SELECT COUNT(*) FROM products WHERE is_active = 1) AS totalProducts,

      (SELECT COUNT(*) FROM categories WHERE is_active = 1) AS totalCategories,

      (
        SELECT ROUND(
          COUNT(CASE WHEN status='completed' THEN 1 END)
          / NULLIF(COUNT(*),0) * 100
        ,2)
        FROM orders
      ) AS conversionRate
  `;

  db.query(sql, (err, rows) => {

    if (err) {
      console.error('Summary API Error:', err);
      return res.json({
        totalOrders: 0,
        totalRevenue: 0,
        totalProducts: 0,
        totalCategories: 0,
        conversionRate: 0
      });
    }

    res.json(rows[0] || {});

  });

});


/* ================= LOW STOCK ================= */
app.get('/api/dashboard/low-stock', ensureAdmin, (req, res) => {

  const sql = `
    SELECT name, stock
    FROM products
    WHERE is_active = 1
    AND stock <= 5
    ORDER BY stock ASC
  `;

  runQuery(res, sql, []);

});


/* ================= OUT OF STOCK ================= */
app.get('/api/dashboard/out-of-stock', ensureAdmin, (req, res) => {

  const sql = `
    SELECT name, stock
    FROM products
    WHERE is_active = 1
    AND stock <= 0
  `;

  runQuery(res, sql, []);

});


/* ================= RECENT ORDERS ================= */
app.get('/api/dashboard/recent-orders', ensureAdmin, (req, res) => {

  const sql = `
    SELECT 
      o.id,
      IFNULL(u.name,'Telegram User') AS customer_name,
      ROUND(o.total_cents/100,2) AS amount,
      o.status,
      o.created_at,
      IFNULL(SUM(oi.quantity),0) AS items
    FROM orders o
    LEFT JOIN users u ON u.id = o.user_id
    LEFT JOIN order_items oi ON oi.order_id = o.id
    GROUP BY o.id
    ORDER BY o.created_at DESC
    LIMIT 5
  `;

  runQuery(res, sql, []);

});



/* ================= TOP CATEGORIES ================= */
app.get('/api/dashboard/top-categories', ensureAdmin, (req, res) => {

  const sql = `
    SELECT 
      c.name,
      ROUND(SUM(oi.quantity * oi.price),2) AS total
    FROM order_items oi
    JOIN products p ON oi.product_id = p.id
    JOIN categories c ON p.category_id = c.id
    JOIN orders o ON oi.order_id = o.id
    WHERE o.status IN ('paid','completed')
    GROUP BY c.id
    ORDER BY total DESC
    LIMIT 5
  `;

  runQuery(res, sql, []);

});



/* =====================================================
   PRODUCTS MODULE (FULL FINAL VERSION)
===================================================== */

/* ================= PRODUCT UPLOAD ================= */
const storage = multer.diskStorage({
  destination: './public/uploads/',
  filename: (req, file, cb) => {
    cb(null, Date.now() + path.extname(file.originalname));
  },
});
const upload = multer({ storage });


/* ================= PRODUCTS LIST ================= */
app.get('/admin/products', ensureAdmin, (req, res) => {

  const limit = 6;
  const page = Math.max(parseInt(req.query.page || '1', 10), 1);
  const offset = (page - 1) * limit;

  const q = (req.query.q || '').trim();
  const like = `%${q}%`;
  const categoryId = (req.query.category_id || '').trim();

  const whereParts = ['p.is_active = 1'];
  const whereParams = [];

  if (q) {
    whereParts.push(`(p.name LIKE ? OR p.description LIKE ? OR c.name LIKE ?)`);
    whereParams.push(like, like, like);
  }

  if (categoryId) {
    whereParts.push(`p.category_id = ?`);
    whereParams.push(categoryId);
  }

  const whereSql = `WHERE ${whereParts.join(' AND ')}`;

  const countSql = `
    SELECT COUNT(*) AS total
    FROM products p
    LEFT JOIN categories c ON p.category_id = c.id
    ${whereSql}
  `;

  const listSql = `
    SELECT p.*, c.name AS category_name
    FROM products p
    LEFT JOIN categories c ON p.category_id = c.id
    ${whereSql}
    ORDER BY p.created_at ASC
    LIMIT ? OFFSET ?
  `;

  db.query(countSql, whereParams, (err, countRows) => {

    if (err) return res.status(500).send('Failed to load products');

    const total = countRows?.[0]?.total || 0;
    const totalPages = Math.max(Math.ceil(total / limit), 1);

    db.query(listSql, [...whereParams, limit, offset], (err2, products) => {

      if (err2) return res.status(500).send('Failed to load products');

      res.render('products', {
        products,
        page,
        totalPages,
        q,
        categoryId: req.query.category_id || ''
      });

    });

  });

});


/* ================= ADD PRODUCT ================= */
/* ================= ADD PRODUCT ================= */

app.get('/admin/addproduct', ensureAdmin, (req, res) => {
  db.query('SELECT * FROM categories WHERE is_active = 1', (err, categories) => {
    res.render('addproduct', {
      categories,
      oldData: {},
      error: null
    });
  });
});

app.post('/admin/addproduct', ensureAdmin, upload.array('images', 10), (req, res) => {
  const { name, description, price, quantity, category_id } = req.body;

  // basic required checks
  if (!name || !price || !quantity || !category_id || !req.files?.length) {
    return db.query('SELECT * FROM categories WHERE is_active = 1', (err, categories) => {
      res.render('addproduct', {
        categories,
        oldData: req.body,
        error: 'All fields + image required'
      });
    });
  }

  // convert + validate numbers
  const stock = Number(quantity);
  const priceNum = Number(price);
  const warehouse_stock = 15000;

  if (Number.isNaN(stock) || stock < 0) return res.send('Invalid quantity');
  if (Number.isNaN(priceNum) || priceNum < 0) return res.send('Invalid price');

  // duplicate name check
  db.query(
    'SELECT id FROM products WHERE name = ? AND is_active = 1',
    [name],
    (err, results) => {
      if (err) {
        console.error(err);
        return res.send('DB error');
      }

      if (results?.length > 0) {
        return db.query('SELECT * FROM categories WHERE is_active = 1', (err2, categories) => {
          res.render('addproduct', {
            categories,
            oldData: req.body,
            error: 'Product already exists'
          });
        });
      }

      // ✅ INSERT product (IMPORTANT: pass the values array!)
      db.query(
        `
        INSERT INTO products
          (name, description, price, warehouse_stock, stock, category_id, is_active)
        VALUES
          (?, ?, ?, ?, ?, ?, 1)
        `,
        [name, description || '', priceNum, warehouse_stock, stock, category_id],
        (err3, result) => {
          if (err3) {
            console.error(err3);
            return res.send(err3.message);
          }

          const productId = result.insertId;

          // insert product images
          const imagesToInsert = req.files.map(f => [productId, '/uploads/' + f.filename]);

          db.query(
            'INSERT INTO product_images (product_id, image_url) VALUES ?',
            [imagesToInsert],
            (err4) => {
              if (err4) {
                console.error(err4);
                return res.send(err4.message);
              }

              // set cover image (default index 0)
              const coverIndex = parseInt(req.body.coverIndex || '0', 10);
              const safeCoverIndex = Number.isNaN(coverIndex)
                ? 0
                : Math.min(Math.max(coverIndex, 0), req.files.length - 1);

              const coverImageUrl = '/uploads/' + req.files[safeCoverIndex].filename;

              db.query(
                'UPDATE products SET image_url = ? WHERE id = ?',
                [coverImageUrl, productId],
                (err5) => {
                  if (err5) {
                    console.error(err5);
                    return res.send(err5.message);
                  }
                  res.redirect('/admin/products');
                }
              );
            }
          );
        }
      );
    }
  );
});

/* ================= EDIT PRODUCT ================= */

app.get('/admin/editproduct/:id', ensureAdmin, async (req, res) => {

  const productId = req.params.id;

  const [productRows] = await db.promise().query(
    'SELECT * FROM products WHERE id = ?',
    [productId]
  );

  if (!productRows.length) return res.redirect('/admin/products');

  const product = productRows[0];

  const [imageRows] = await db.promise().query(`
    SELECT id, image_url
    FROM product_images
    WHERE product_id = ?
    ORDER BY (image_url = ?) DESC, id ASC
  `, [productId, product.image_url]);

  product.images = imageRows;

  const [categories] = await db.promise().query(
    'SELECT * FROM categories WHERE is_active = 1'
  );

  res.render('editProduct', { product, categories });

});


app.post('/admin/editproduct/:id', ensureAdmin, upload.array('images', 10), (req, res) => {

  const productId = req.params.id;

  const {
    name,
    description,
    price,
    quantity,
    category_id,
    removedImages,
    coverImageId
  } = req.body;

  db.query(`
    UPDATE products
    SET
      name = ?,
      description = ?,
      price = ?,
      stock = ?,
      category_id = ?
    WHERE id = ?
  `,
  [
    name,
    description || '',
    price,
    quantity,
    category_id,
    productId
  ],
  err => {

    if (err) return res.send('DB error');

    const removedIds = (removedImages || '')
      .split(',')
      .map(x => x.trim())
      .filter(Boolean);

    const removeImagesPromise = new Promise(resolve => {

      if (!removedIds.length) return resolve();

      db.query(
        'SELECT id, image_url FROM product_images WHERE id IN (?)',
        [removedIds],
        (err2, rows) => {

          rows?.forEach(r => {
            const filePath = path.join(__dirname, 'public', r.image_url.replace('/', ''));
            fs.unlink(filePath, () => {});
          });

          db.query('DELETE FROM product_images WHERE id IN (?)', [removedIds], () => resolve());

        }
      );

    });

    const addImagesPromise = new Promise(resolve => {

      if (!req.files?.length) return resolve();

      const newImages = req.files.map(f => [productId, '/uploads/' + f.filename]);

      db.query(
        'INSERT INTO product_images (product_id, image_url) VALUES ?',
        [newImages],
        () => resolve()
      );

    });

    Promise.all([removeImagesPromise, addImagesPromise]).then(() => {

      const setCover = (img) => {
        db.query(
          'UPDATE products SET image_url=? WHERE id=?',
          [img, productId],
          () => res.redirect('/admin/products')
        );
      };

      if (coverImageId) {

        db.query(
          'SELECT image_url FROM product_images WHERE id=?',
          [coverImageId],
          (err3, rows) => {

            if (rows?.length) return setCover(rows[0].image_url);

            db.query(
              'SELECT image_url FROM product_images WHERE product_id=? LIMIT 1',
              [productId],
              (err4, rows2) => setCover(rows2?.[0]?.image_url || null)
            );

          }
        );

      }

    });

  });

});


/* ================= CHECK DUPLICATE PRODUCT NAME ================= */

app.get('/admin/check-product-name', ensureAdmin, (req, res) => {

  const { name } = req.query;

  if (!name) return res.json({ exists: false });

  db.query(
    'SELECT id FROM products WHERE name = ? AND is_active = 1',
    [name],
    (err, results) => {

      if (err) return res.json({ exists: false });

      res.json({
        exists: results.length > 0
      });

    }
  );

});


/* ================= DELETE PRODUCT ================= */

app.post('/admin/deleteproduct/:id', ensureAdmin, (req, res) => {

  const productId = req.params.id;

  db.query(
    'UPDATE products SET is_active = 0 WHERE id = ?',
    [productId],
    () => res.redirect('/admin/products')
  );

});

/* ================= CATEGORIES ================= */
app.get('/admin/categories', ensureAdmin, (req, res) => {
  const limit = 6;
  const page = Math.max(parseInt(req.query.page || '1', 10), 1);
  const offset = (page - 1) * limit;

  const countSql = `
    SELECT COUNT(*) AS total
    FROM categories
    WHERE is_active = 1
  `;

  const listSql = `
    SELECT *
    FROM categories
    WHERE is_active = 1
    ORDER BY created_at ASC
    LIMIT ? OFFSET ?
  `;

  const inactiveSql = `
    SELECT *
    FROM categories
    WHERE is_active = 0
    ORDER BY updated_at DESC
  `;

  db.query(countSql, (err, countRows) => {
    if (err) return res.send('Failed to load categories');

    const total = countRows[0].total;
    const totalPages = Math.max(Math.ceil(total / limit), 1);

    db.query(listSql, [limit, offset], (err2, categories) => {
      if (err2) return res.send('Failed to load categories');

      db.query(inactiveSql, (err3, inactiveCategories) => {
        if (err3) inactiveCategories = [];

        res.render('categories', {
          categories,
          inactiveCategories,
          page,
          totalPages
        });
      });
    });
  });
});

app.get('/admin/categories/add', ensureAdmin, (req, res) => {
  res.render('addcategories');
});

app.post('/admin/categories/reactivate/:id', ensureAdmin, (req, res) => {
  const id = req.params.id;

  db.query(
    'UPDATE categories SET is_active = 1, updated_at = NOW() WHERE id = ?',
    [id],
    err => {
      if (err) {
        console.error(err);
        return res.redirect('/admin/categories');
      }
      res.redirect('/admin/categories');
    }
  );
});

app.post('/admin/categories/add', ensureAdmin, (req, res) => {
  const { name, description, is_active } = req.body;

  if (!name || !name.trim()) {
    return res.render('addcategories', {
      error: 'Category name is required',
    });
  }

  // DUPLICATE CHECK
  db.query(
    'SELECT id FROM categories WHERE LOWER(name) = LOWER(?)',
    [name.trim()],
    (err, results) => {
      if (err) {
        console.error(err);
        return res.render('addcategories', {
          error: 'Database error',
        });
      }

      if (results.length > 0) {
        return res.render('addcategories', {
          error: 'This category name already exists',
        });
      }

      // UNIQUE IF UNIQUE
      db.query(
        `INSERT INTO categories (name, description, is_active)
         VALUES (?, ?, ?)`,
        [name.trim(), description || null, is_active ? 1 : 0],
        err2 => {
          if (err2) {
            console.error(err2);
            return res.render('addcategories', {
              error: 'Database error',
            });
          }
          res.redirect('/admin/categories');
        }
      );
    }
  );
});

/* ================= EDIT CATEGORY ================= */
// GET Edit Category page
app.get('/admin/categories/edit/:id', ensureAdmin, (req, res) => {
  const id = req.params.id;

  db.query('SELECT * FROM categories WHERE id = ?', [id], (err, rows) => {
    if (err || !rows.length) return res.redirect('/admin/categories');
    res.render('editcategories', { category: rows[0], error: null });
  });
});

// POST Update Category
app.post('/admin/categories/edit/:id', ensureAdmin, (req, res) => {
  const id = req.params.id;
  const { name, description, is_active } = req.body;

  if (!name || !name.trim()) {
    return db.query(
      'SELECT * FROM categories WHERE id = ?',
      [id],
      (err, rows) => {
        return res.render('editcategories', {
          category: rows[0],
          error: 'Category name is required',
        });
      }
    );
  }

  // DUPLICATE CATEGORY NAME CHECK
  db.query(
    'SELECT id FROM categories WHERE LOWER(name) = LOWER(?) AND id != ?',
    [name.trim(), id],
    (err, results) => {
      if (err) {
        console.error(err);
        return res.render('editcategories', {
          category: { id, name, description, is_active },
          error: 'Database error',
        });
      }

      if (results.length > 0) {
        return res.render('editcategories', {
          category: { id, name, description, is_active },
          error: 'Another category with this name already exists',
        });
      }

      // Update if unique
      db.query(
        `UPDATE categories
         SET name = ?, description = ?, is_active = ?, updated_at = NOW()
         WHERE id = ?`,
        [name.trim(), description || null, is_active ? 1 : 0, id],
        err2 => {
          if (err2) {
            console.error(err2);
            return res.render('editcategories', {
              category: { id, name, description, is_active },
              error: 'Database error',
            });
          }

          res.redirect('/admin/categories');
        }
      );
    }
  );
});

/* ================= Analytics ================= */
app.get('/analytics', ensureAdmin, (req, res) => {
  res.render('analytics', { activePage: 'analytics' });
});


/* ================= Analytics ================= */
app.get('/analytics', ensureAdmin, (req, res) => {
  res.render('analytics', { activePage: 'analytics' });
});


/* =========================
   SUMMARY (Top Cards)
========================= */
app.get('/api/analytics/summary', ensureAdmin, (req, res) => {

  const period = req.query.period || 'today';

  let orderDateFilter = '1=1';
  let userDateFilter = '1=1';

  if (period === 'today') {
    orderDateFilter = 'DATE(o.created_at) = CURDATE()';
    userDateFilter = 'DATE(u.created_at) = CURDATE()';
  } else {
    orderDateFilter = `o.created_at >= DATE_SUB(NOW(), INTERVAL ${Number(period)} DAY)`;
    userDateFilter = `u.created_at >= DATE_SUB(NOW(), INTERVAL ${Number(period)} DAY)`;
  }

  const sql = `
    SELECT
      -- Revenue
      ROUND(IFNULL(SUM(o.total_cents),0)/100,2) AS revenue,

      -- Orders
      COUNT(DISTINCT o.id) AS orders,

      -- Total Customers (FROM USERS TABLE ✅)
      (SELECT COUNT(*) FROM users u WHERE ${userDateFilter}) AS customers

    FROM orders o
    WHERE ${orderDateFilter}
  `;

  db.query(sql, (err, results) => {
    if (err) {
      console.error('Analytics summary error:', err);
      return res.json({ revenue: 0, orders: 0, customers: 0 });
    }

    res.json(results[0] || { revenue: 0, orders: 0, customers: 0 });
  });
});



/* =========================
   SALES OVER TIME (Line Chart)
========================= */
app.get('/api/analytics/sales-over-time', ensureAdmin, (req, res) => {

  const period = req.query.period || '30';

  let dateFilter = '';
  let params = [];

  if (period === 'today') {
    dateFilter = 'DATE(created_at) = CURDATE()';
  } else {
    dateFilter = 'created_at >= DATE_SUB(NOW(), INTERVAL ? DAY)';
    params.push(Number(period));
  }

  const sql = `
    SELECT
      DATE(created_at) AS date,
      ROUND(SUM(total_cents)/100,2) AS revenue
    FROM orders
    WHERE ${dateFilter}
    GROUP BY DATE(created_at)
    ORDER BY DATE(created_at)
  `;

  db.query(sql, params, (err, rows) => {
    if (err) {
      console.error(err);
      return res.json([]);
    }
    res.json(rows);
  });

});


/* =========================
   TOP SELLING PRODUCTS
========================= */
app.get('/api/analytics/top-products', ensureAdmin, (req, res) => {

  const limit = Number(req.query.limit || 5);
  const period = req.query.period || '30';

  let dateFilter = '';
  let params = [];

  if (period === 'today') {
    dateFilter = 'DATE(o.created_at) = CURDATE()';
  } else {
    dateFilter = 'o.created_at >= DATE_SUB(NOW(), INTERVAL ? DAY)';
    params.push(Number(period));
  }

  params.push(limit);

  const sql = `
    SELECT 
      p.name,
      IFNULL(SUM(oi.qty), 0) AS total_sold
    FROM products p
    LEFT JOIN order_items oi ON oi.product_id = p.id
    LEFT JOIN orders o ON o.id = oi.order_id
    WHERE ${dateFilter}
    GROUP BY p.id
    ORDER BY total_sold DESC
    LIMIT ?
  `;

  db.query(sql, params, (err, rows) => {
    if (err) {
      console.error(err);
      return res.json([]);
    }
    res.json(rows);
  });

});

/* =========================
   REVENUE BY CATEGORY
========================= */
app.get('/api/analytics/revenue-by-category', ensureAdmin, (req, res) => {

  const period = req.query.period || '30';

  let dateFilter = '';
  let params = [];

  if (period === 'today') {
    dateFilter = 'DATE(o.created_at) = CURDATE()';
  } else {
    dateFilter = 'o.created_at >= DATE_SUB(NOW(), INTERVAL ? DAY)';
    params.push(Number(period));
  }

  const sql = `
    SELECT 
      c.name AS category,
      ROUND(IFNULL(SUM(oi.quantity * oi.price),0), 2) AS revenue
    FROM categories c
    LEFT JOIN products p ON p.category_id = c.id
    LEFT JOIN order_items oi ON oi.product_id = p.id
    LEFT JOIN orders o ON o.id = oi.order_id
    WHERE ${dateFilter}
    GROUP BY c.id
    ORDER BY revenue DESC
  `;

  db.query(sql, params, (err, rows) => {
    if (err) {
      console.error(err);
      return res.json([]);
    }
    res.json(rows);
  });

});

/* =========================
   SALES OVER TIME (Line Chart)
========================= */
app.get('/api/analytics/sales-over-time', ensureAdmin, (req, res) => {

  const period = req.query.period || '30';

  let dateFilter = '';
  let params = [];

  if (period === 'today') {
    dateFilter = 'DATE(created_at) = CURDATE()';
  } else {
    dateFilter = 'created_at >= DATE_SUB(NOW(), INTERVAL ? DAY)';
    params.push(Number(period));
  }

  const sql = `
    SELECT
      DATE(created_at) AS date,
      ROUND(SUM(total_cents)/100,2) AS revenue
    FROM orders
    WHERE ${dateFilter}
    GROUP BY DATE(created_at)
    ORDER BY DATE(created_at)
  `;

  db.query(sql, params, (err, rows) => {
    if (err) {
      console.error(err);
      return res.json([]);
    }
    res.json(rows);
  });

});

/* =========================
   TOP SELLING PRODUCTS
========================= */
app.get('/api/analytics/top-products', ensureAdmin, (req, res) => {

  const limit = Number(req.query.limit || 5);
  const period = req.query.period || '30';

  let dateFilter = '';
  let params = [];

  if (period === 'today') {
    dateFilter = 'DATE(o.created_at) = CURDATE()';
  } else {
    dateFilter = 'o.created_at >= DATE_SUB(NOW(), INTERVAL ? DAY)';
    params.push(Number(period));
  }

  params.push(limit);

  const sql = `
    SELECT 
      p.name,
      IFNULL(SUM(oi.qty), 0) AS total_sold
    FROM products p
    LEFT JOIN order_items oi ON oi.product_id = p.id
    LEFT JOIN orders o ON o.id = oi.order_id
    WHERE ${dateFilter}
    GROUP BY p.id
    ORDER BY total_sold DESC
    LIMIT ?
  `;

  db.query(sql, params, (err, rows) => {
    if (err) {
      console.error(err);
      return res.json([]);
    }
    res.json(rows);
  });

});

/* =========================
   REVENUE BY CATEGORY
========================= */
app.get('/api/analytics/revenue-by-category', ensureAdmin, (req, res) => {

  const period = req.query.period || '30';

  let dateFilter = '';
  let params = [];

  if (period === 'today') {
    dateFilter = 'DATE(o.created_at) = CURDATE()';
  } else {
    dateFilter = 'o.created_at >= DATE_SUB(NOW(), INTERVAL ? DAY)';
    params.push(Number(period));
  }

  const sql = `
    SELECT 
      c.name AS category,
      ROUND(IFNULL(SUM(oi.quantity * oi.price),0), 2) AS revenue
    FROM categories c
    LEFT JOIN products p ON p.category_id = c.id
    LEFT JOIN order_items oi ON oi.product_id = p.id
    LEFT JOIN orders o ON o.id = oi.order_id
    WHERE ${dateFilter}
    GROUP BY c.id
    ORDER BY revenue DESC
  `;

  db.query(sql, params, (err, rows) => {
    if (err) {
      console.error(err);
      return res.json([]);
    }
    res.json(rows);
  });

});

/* ================= Discounts ================= */

/* ===== LIST ===== */
app.get('/discounts', ensureAdmin, (req, res) => {
  db.query(
    'SELECT * FROM discounts WHERE is_active = 1 ORDER BY created_at DESC',
    (err, results) => {
      if (err) {
        console.error("DISCOUNT LIST ERROR:", err);
        return res.status(500).send(err.message);
      }
      res.render('discounts', { discounts: results });
    }
  );
});

/* ===== ADD PAGE ===== */
app.get('/discounts/add', ensureAdmin, (req, res) => {
  db.query(
    'SELECT id, name FROM categories ORDER BY name ASC',
    (err, categories) => {
      if (err) categories = [];
      res.render('adddiscounts', { categories });
    }
  );
});

/* ===== ADD SUBMIT ===== */
app.post('/discounts/add', ensureAdmin, (req, res) => {

  const {
    code,
    type,
    value,
    usage_limit,
    minimum_purchase,
    valid_until,
    apply_scope,
    selected_categories
  } = req.body;

  // ⭐ SAFE checkbox handling
  const isActiveValue = req.body.is_active ? 1 : 0;

  const sql = `
    INSERT INTO discounts
    (code, type, value, minimum_purchase, usage_limit, used, valid_until, is_active, created_at)
    VALUES (?, ?, ?, ?, ?, 0, ?, ?, NOW())
  `;

  db.query(
    sql,
    [
      code,
      type,
      value,
      minimum_purchase || 0,
      usage_limit || 0,
      valid_until || null,
      isActiveValue
    ],
    (err, result) => {

      if (err) {
        console.error("DISCOUNT ADD ERROR:", err);
        return res.status(500).send(err.message);
      }

      const discountId = result.insertId;

      /* Apply ALL */
      if (apply_scope === 'all') {
        return res.redirect('/discounts');
      }

      /* Apply SELECTED */
      if (apply_scope === 'selected' && selected_categories) {

        const categoryIds = Array.isArray(selected_categories)
          ? selected_categories
          : [selected_categories];

        const values = categoryIds.map(c => [discountId, c]);

        return db.query(
          'INSERT INTO discount_categories (discount_id, category_id) VALUES ?',
          [values],
          (err2) => {
            if (err2) {
              console.error("DISCOUNT CATEGORY MAP ERROR:", err2);
              return res.status(500).send(err2.message);
            }
            res.redirect('/discounts');
          }
        );
      }

      res.redirect('/discounts');
    }
  );
});

/* ===== EDIT PAGE ===== */
app.get('/discounts/edit/:id', ensureAdmin, (req, res) => {

  const id = req.params.id;

  db.query(
    'SELECT * FROM discounts WHERE id=?',
    [id],
    (err, rows) => {

      if (err || !rows.length) {
        console.error("DISCOUNT LOAD ERROR:", err);
        return res.status(404).send('Discount not found');
      }

      const discount = rows[0];

      db.query(
        'SELECT id,name FROM categories ORDER BY name ASC',
        (err2, categories) => {

          if (err2) categories = [];

          db.query(
            'SELECT category_id FROM discount_categories WHERE discount_id=?',
            [id],
            (err3, mapRows) => {

              const selectedCategoryIds = err3
                ? []
                : mapRows.map(r => r.category_id);

              const applyScope =
                selectedCategoryIds.length ? 'selected' : 'all';

              res.render('editdiscounts', {
                discount,
                categories,
                selectedCategoryIds,
                applyScope
              });

            }
          );
        }
      );
    }
  );
});

/* ===== EDIT SUBMIT ===== */
app.post('/discounts/edit/:id', ensureAdmin, (req, res) => {

  const id = req.params.id;

  const {
    code,
    type,
    value,
    usage_limit,
    minimum_purchase,
    valid_until,
    apply_scope,
    selected_categories
  } = req.body;

  const isActiveValue = req.body.is_active ? 1 : 0;

  const sql = `
    UPDATE discounts SET
      code=?,
      type=?,
      value=?,
      minimum_purchase=?,
      usage_limit=?,
      valid_until=?,
      is_active=?
    WHERE id=?
  `;

  db.query(
    sql,
    [
      code,
      type,
      value,
      minimum_purchase || 0,
      usage_limit || 0,
      valid_until || null,
      isActiveValue,
      id
    ],
    (err) => {

      if (err) {
        console.error("DISCOUNT UPDATE ERROR:", err);
        return res.status(500).send(err.message);
      }

      /* Clear old mapping */
      db.query(
        'DELETE FROM discount_categories WHERE discount_id=?',
        [id],
        (err2) => {

          if (err2) {
            console.error("DISCOUNT MAP DELETE ERROR:", err2);
            return res.status(500).send(err2.message);
          }

          if (apply_scope === 'all') {
            return res.redirect('/discounts');
          }

          if (apply_scope === 'selected' && selected_categories) {

            const categoryIds = Array.isArray(selected_categories)
              ? selected_categories
              : [selected_categories];

            const values = categoryIds.map(c => [id, c]);

            return db.query(
              'INSERT INTO discount_categories (discount_id, category_id) VALUES ?',
              [values],
              (err3) => {

                if (err3) {
                  console.error("DISCOUNT MAP INSERT ERROR:", err3);
                  return res.status(500).send(err3.message);
                }

                res.redirect('/discounts');
              }
            );
          }

          res.redirect('/discounts');

        }
      );
    }
  );
});

/* ===== DELETE ===== */
app.post('/discounts/delete/:id', ensureAdmin, (req, res) => {
  db.query(
    'UPDATE discounts SET is_active = 0 WHERE id = ?',
    [req.params.id],
    err => {
      if (err) {
        console.error("DISCOUNT DELETE ERROR:", err);
        return res.status(500).send(err.message);
      }
      res.redirect('/discounts');
    }
  );
});

/* ================== Inventory ================== */
app.get('/admin/inventory', ensureAdmin, (req, res) => {

  const sql = `
    SELECT
      p.id,
      p.name,
      p.price,
      p.stock,
      p.warehouse_stock,
      c.name AS category_name
    FROM products p
    LEFT JOIN categories c ON p.category_id = c.id
    WHERE p.is_active = 1
    ORDER BY p.name
  `;

  db.query(sql, (err, products) => {

    if (err) return res.sendStatus(500);

    const stats = {
      total: products.length,
      inStock: products.filter(p => p.stock > 10).length,
      lowStock: products.filter(p => p.stock > 0 && p.stock <= 10).length,
      outOfStock: products.filter(p => p.stock === 0).length,
    };

    res.render('inventory', { products, stats });

  });

});

app.get('/admin/inventory/update/:id', ensureAdmin, (req, res) => {
  const productId = req.params.id;

  db.query('SELECT id, name, stock FROM products WHERE id = ?', [productId], (err, results) => {
    if (err || !results.length) return res.redirect('/admin/inventory');
    res.render('updateStock', { product: results[0], error: null });
  });
});

app.post('/admin/inventory/update/:id', ensureAdmin, async (req, res) => {

  const productId = req.params.id;
  const newStock = Number(req.body.stock);

  if (isNaN(newStock) || newStock < 0) {
    return res.send('Invalid stock');
  }

  try {

    const [rows] = await db.promise().query(
      `SELECT stock, warehouse_stock FROM products WHERE id = ?`,
      [productId]
    );

    if (!rows.length) return res.send('Product not found');

    const oldStock = rows[0].stock;
    const warehouse = rows[0].warehouse_stock;

    const diff = newStock - oldStock;

    if (warehouse - diff < 0) {
      return res.send('❌ Not enough warehouse stock');
    }

    await db.promise().query(`
      UPDATE products
      SET
        stock = ?,
        warehouse_stock = warehouse_stock - ?
      WHERE id = ?
    `,
    [
      newStock,
      diff,
      productId
    ]);

    res.redirect('/admin/inventory');

  } catch (err) {
    console.error(err);
    res.send('Update failed');
  }

});

/* ================= USERS================= */
app.get('/users', ensureAdmin, (req, res) => {
  const sql = `
    SELECT
      id,
      name,
      email,
      telegram_id,
      phone_number,
      created_at
    FROM users
    WHERE is_active = 1
    ORDER BY created_at DESC
  `;

  db.query(sql, (err, users) => {
    if (err) {
      console.error('Users fetch error:', err);
      return res.render('users', { users: [] });
    }

    res.render('users', { users });
  });
});

app.get('/api/users/:id', ensureAdmin, (req, res) => {
  const userId = req.params.id;

  const sql = `
    SELECT
      id,
      name,
      email,
      telegram_id,
      phone_number,
      address,
      delivery_address,
      created_at
    FROM users
    WHERE id = ?
  `;

  db.query(sql, [userId], (err, results) => {
    if (err) {
      console.error(err);
      return res.status(500).json({ error: 'Database error' });
    }

    if (!results.length) {
      return res.status(404).json({ error: 'User not found' });
    }

    res.json(results[0]);
  });
});

/* ================= ORDERS ================= */

/* ===== LIST ORDERS ===== */
app.get('/orders', ensureAdmin, (req, res) => {

  const sql = `
    SELECT
      o.id,
      o.full_name,
      o.total_cents / 100 AS total_cents,
      o.status,
      o.order_date,
      IFNULL(SUM(oi.quantity), 0) AS items
    FROM orders o
    LEFT JOIN order_items oi
      ON o.id = oi.order_id
    GROUP BY o.id
    ORDER BY o.order_date DESC
  `;

  db.query(sql, (err, orders) => {
    if (err) {
      console.error('Orders fetch error:', err);
      return res.render('orders', { orders: [] });
    }

    res.render('orders', { orders });
  });
});

/* ===== ORDER DETAILS ===== */
app.get('/orders/:id', ensureAdmin, (req, res) => {

  const orderId = req.params.id;

  /* 1️⃣ Order header info */
  const orderSql = `
    SELECT
      id,
      full_name,
      address,
      status,
      order_date,
      total_cents / 100 AS total
    FROM orders
    WHERE id = ?
  `;

  /* 2️⃣ Items in order (stored snapshot) */
  const itemsSql = `
    SELECT
      product_name,
      quantity,
      price,
      (quantity * price) AS subtotal
    FROM order_items
    WHERE order_id = ?
  `;

  db.query(orderSql, [orderId], (err, orderRows) => {
    if (err || !orderRows.length) {
      console.error('Order not found:', err);
      return res.redirect('/orders');
    }

    db.query(itemsSql, [orderId], (err2, items) => {
      if (err2) {
        console.error('Order items error:', err2);
        return res.redirect('/orders');
      }

      res.render('orderDetails', {
        order: orderRows[0],
        items
      });
    });
  });

});

/* ================= STOCK NOTIFICATIONS ================= */
app.get('/api/notifications/stock', ensureAdmin, (req, res) => {

  const sql = `
    SELECT
      name,
      stock,
      warehouse_stock
    FROM products
    WHERE is_active = 1
    AND (
      stock <= 5
      OR warehouse_stock <= 2000
    )
    ORDER BY stock ASC
  `;

  db.query(sql, (err, rows) => {

    if (err) {
      console.error(err);
      return res.json([]);
    }

    const notifications = rows.map(p => {

      if (p.stock === 0) {
        return `🚨 ${p.name} is OUT OF STOCK`;
      }

      if (p.stock <= 5) {
        return `⚠️ ${p.name} low Telegram stock (${p.stock})`;
      }

      if (p.warehouse_stock <= 2000) {
        return `📦 ${p.name} warehouse running low (${p.warehouse_stock})`;
      }

    });

    res.json(notifications.filter(Boolean));

  });

});

/* ================= SERVER ================= */
const PORT = process.env.PORT || 3000;

app.listen(3000, () => {
  console.log('Server running on http://localhost:3000');
});

