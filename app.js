const express = require('express');
const mysql = require('mysql2');
const bcrypt = require('bcrypt');
const session = require('express-session');
const multer = require('multer');
const path = require('path');
require('dotenv').config();


const app = express();
/* ================= MULTER CONFIG ================= */

const storage = multer.diskStorage({
  destination: (req, file, cb) => {
    cb(null, 'public/uploads');
  },
  filename: (req, file, cb) => {
    const uniqueName =
      Date.now() + '-' + Math.round(Math.random() * 1e9) + path.extname(file.originalname);
    cb(null, uniqueName);
  }
});

const upload = multer({
  storage: storage,
  limits: { fileSize: 5 * 1024 * 1024 } // 5MB per file
});
const fs = require('fs');
/* ================= DATABASE ================= */
require('dotenv').config();

const db = mysql.createConnection({
  host: process.env.MYSQLHOST,
  user: process.env.MYSQLUSER,
  password: process.env.MYSQLPASSWORD,
  database: process.env.MYSQL_DATABASE,
  port: process.env.MYSQLPORT,
});

/* ================= MIDDLEWARE ================= */
app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(express.static('public'));
app.use('/uploads', express.static('public/uploads'));

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
  db.query(
    'SELECT * FROM orders ORDER BY created_at DESC',
    (err, orders) => {
      if (err) return res.send('Failed to load orders');
      res.render('adminOrders', { orders });
    }
  );
});

/* ================= DASHBOARD APIs ================= */
app.get('/api/dashboard/summary', ensureAdmin, (req, res) => {
  const sql = `
    SELECT
      (SELECT COUNT(*) FROM orders) AS totalOrders,
      (SELECT IFNULL(SUM(total_amount), 0) FROM orders) AS totalRevenue,
      (SELECT COUNT(*) FROM products WHERE is_active = 1) AS totalProducts,
      (SELECT COUNT(*) FROM categories WHERE is_active = 1) AS totalCategories,
      0 AS productViews,
      0 AS conversionRate
  `;

  db.query(sql, (err, results) => {
    if (err) {
      console.error(err);
      return res.status(500).json({ message: 'Database error' });
    }

    if (!results || results.length === 0) {
      return res.status(404).json({ message: 'No data found' });
    }

    res.json(results[0]);
  });
});

app.get('/api/dashboard/low-stock', ensureAdmin, (req, res) => {
  db.query(
    'SELECT name, stock FROM products WHERE stock <= 5 AND is_active = 1',
    (err, rows) => {
      if (err) return res.json([]);
      res.json(rows);
    }
  );
});

app.get('/api/dashboard/recent-orders', ensureAdmin, (req, res) => {
  db.query(
    `SELECT o.id, o.total_amount, o.status, o.created_at,
            'Telegram User' AS username,
            (SELECT SUM(quantity) FROM order_items WHERE order_id = o.id) AS items
     FROM orders o
     ORDER BY o.created_at DESC
     LIMIT 5`,
    (err, rows) => {
      if (err) return res.json([]);
      res.json(rows);
    }
  );
});

app.get('/api/dashboard/out-of-stock', ensureAdmin, (req, res) => {
  const sql = `
    SELECT name, stock
    FROM products
    WHERE is_active = 1 AND stock <= 0
  `;

  db.query(sql, (err, rows) => {
    if (err) return res.json([]);
    res.json(rows);
  });
});

/* ================= PRODUCTS LIST ================= */
app.get('/admin/products', ensureAdmin, (req, res) => {
  const limit = 6;
  const page = Math.max(parseInt(req.query.page || '1', 10), 1);
  const offset = (page - 1) * limit;

  const q = (req.query.q || '').trim();
  const like = `%${q}%`;

  const categoryId = (req.query.category_id || '').trim(); // ✅ new

  // ✅ build WHERE conditions
  const whereParts = [];
  const whereParams = [];

  if (q) {
    whereParts.push(`(p.name LIKE ? OR p.description LIKE ? OR c.name LIKE ?)`);
    whereParams.push(like, like, like);
  }

  if (categoryId) {
    whereParts.push(`p.category_id = ?`);
    whereParams.push(categoryId);
  }

  const whereSql = whereParts.length ? `WHERE ${whereParts.join(' AND ')}` : ``;

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
    if (err) {
      console.error(err);
      return res.status(500).send('Failed to load products');
    }

    const total = countRows?.[0]?.total || 0;
    const totalPages = Math.max(Math.ceil(total / limit), 1);

    db.query(listSql, [...whereParams, limit, offset], (err2, products) => {
      if (err2) {
        console.error(err2);
        return res.status(500).send('Failed to load products');
      }

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
// GET Add Product
app.get('/admin/addproduct', ensureAdmin, (req, res) => {
  db.query('SELECT * FROM categories WHERE is_active = 1', (err, categories) => {
    if (err) return res.send('Failed to load categories');
    res.render('addproduct', { categories, oldData: {}, error: null });
  });
});

// POST Add Product
app.post('/admin/addproduct', ensureAdmin, upload.array('images', 10), (req, res) => {
  const { name, description, price, quantity, category_id } = req.body;

  if (
    !name ||
    !price ||
    !quantity ||
    !category_id ||
    !req.files ||
    req.files.length === 0
  ) {
    return db.query('SELECT * FROM categories WHERE is_active = 1', (err, categories) => {
      return res.render('addproduct', {
        categories,
        oldData: req.body,
        error: 'All fields are required and at least one image must be uploaded',
      });
    });
  }

  // Duplicate name check
  db.query(
    'SELECT id FROM products WHERE name = ? AND is_active = 1',
    [name],
    (err, results) => {
      if (err) {
        console.error(err);
        return db.query('SELECT * FROM categories WHERE is_active = 1', (err2, categories) => {
          return res.render('addproduct', {
            categories,
            oldData: req.body,
            error: 'Database error',
          });
        });
      }

      if (results.length > 0) {
        return db.query('SELECT * FROM categories WHERE is_active = 1', (err2, categories) => {
          return res.render('addproduct', {
            categories,
            oldData: req.body,
            error: 'Product with this name already exists',
          });
        });
      }

      // 1) insert product (set stock from quantity so your table shows correct stock)

db.query(
  `
  INSERT INTO products (
    name,
    description,
    price,
    telegram_stock,
    warehouse_stock,
    stock,
    category_id,
    is_active
  )
  VALUES (?, ?, ?, ?, ?, ?, ?, 1)
  `,
  [
    name,
    description || '',
    price,
    quantity,   // telegram_stock
    0,          // warehouse_stock (inventory page later)
    quantity,   // total stock
    category_id
  ],
  (err3, result) => {
    if (err3) {
      console.error(err3);
      return db.query(
        'SELECT * FROM categories WHERE is_active = 1',
        (err4, categories) => {
          return res.render('addproduct', {
            categories,
            oldData: req.body,
            error: 'DB error',
          });
        }
      );
    }

    const productId = result.insertId;

    // 👉 continue image upload logic here
  }
);


          // 2) insert all images
          const imagesToInsert = req.files.map(f => [productId, '/uploads/' + f.filename]);
          db.query(
            'INSERT INTO product_images (product_id, image_url) VALUES ?',
            [imagesToInsert],
            err5 => {
              if (err5) console.error(err5);

              // 3) set cover based on coverIndex (defaults to 0)
              const coverIndex = Number.isFinite(parseInt(req.body.coverIndex))
                ? parseInt(req.body.coverIndex)
                : 0;
              const safeIndex = Math.min(Math.max(coverIndex, 0), req.files.length - 1);
              const coverImageUrl = '/uploads/' + req.files[safeIndex].filename;

              db.query(
                'UPDATE products SET image_url = ? WHERE id = ?',
                [coverImageUrl, productId],
                err6 => {
                  if (err6) console.error(err6);
                  res.redirect('/admin/products');
                }
              );
            }
          );
        }
      );
    }
  );

/* ================= EDIT PRODUCT ================= */
// GET edit product
app.get('/admin/editproduct/:id', ensureAdmin, async (req, res) => {
  const productId = req.params.id;

  try {
    const [productRows] = await db.promise().query(
      'SELECT * FROM products WHERE id = ?',
      [productId]
    );

    if (!productRows.length) return res.redirect('/admin/products');

    const product = productRows[0];

    // IMPORTANT:
    // Order images so the current cover (products.image_url) appears first in edit UI
    const [imageRows] = await db.promise().query(
      `
      SELECT id, image_url
      FROM product_images
      WHERE product_id = ?
      ORDER BY (image_url = ?) DESC, id ASC
      `,
      [productId, product.image_url]
    );

    product.images = imageRows;

    const [categories] = await db.promise().query(
      'SELECT * FROM categories WHERE is_active = 1'
    );

    res.render('editproduct', { product, categories });
  } catch (err) {
    console.error(err);
    res.redirect('/admin/products');
  }
});

// POST edit product
app.post('/admin/editproduct/:id', ensureAdmin, upload.array('images', 10), (req, res) => {
  const productId = req.params.id;
  const { name, description, price, quantity, category_id, removedImages, coverImageId } = req.body;

  // 1) update product fields (also keep stock in sync with quantity)
  db.query(
    `UPDATE products SET name=?, description=?, price=?, quantity=?, stock=?, category_id=? WHERE id=?`,
    [name, description || '', price, quantity, quantity, category_id, productId],
    err => {
      if (err) {
        console.error(err);
        return res.send('DB error updating product');
      }

      const removedIds = (removedImages || '')
        .split(',')
        .map(s => s.trim())
        .filter(Boolean);

      // 2) if removing images: fetch urls (to delete files), then delete DB rows
      const removeImagesPromise = new Promise(resolve => {
        if (!removedIds.length) return resolve();

        db.query(
          'SELECT id, image_url FROM product_images WHERE id IN (?)',
          [removedIds],
          (err2, rows) => {
            if (!err2 && rows && rows.length) {
              rows.forEach(r => {
                if (!r.image_url) return;
                const filePath = path.join(
                  __dirname,
                  'public',
                  r.image_url.startsWith('/') ? r.image_url.slice(1) : r.image_url
                );
                fs.unlink(filePath, () => {});
              });
            }

            db.query(
              'DELETE FROM product_images WHERE id IN (?)',
              [removedIds],
              () => resolve()
            );
          }
        );
      });

      // 3) add new images
      const addImagesPromise = new Promise(resolve => {
        if (!req.files || !req.files.length) return resolve();
        const newImages = req.files.map(f => [productId, '/uploads/' + f.filename]);
        db.query(
          'INSERT INTO product_images (product_id, image_url) VALUES ?',
          [newImages],
          () => resolve()
        );
      });

      Promise.all([removeImagesPromise, addImagesPromise]).then(() => {
        // 4) update cover (IMPORTANT: coverImageId is computed from FIRST thumbnail in edit UI)
        // If coverImageId is missing or was deleted, fallback to first remaining image.
        const setCover = (imageUrlOrNull) => {
          db.query(
            'UPDATE products SET image_url = ? WHERE id = ?',
            [imageUrlOrNull, productId],
            err9 => {
              if (err9) console.error(err9);
              res.redirect('/admin/products');
            }
          );
        };

        if (coverImageId) {
          db.query(
            'SELECT image_url FROM product_images WHERE id = ? AND product_id = ?',
            [coverImageId, productId],
            (err8, rows) => {
              if (!err8 && rows && rows.length) {
                return setCover(rows[0].image_url);
              }

              // fallback: first image for this product
              db.query(
                'SELECT image_url FROM product_images WHERE product_id = ? ORDER BY id ASC LIMIT 1',
                [productId],
                (err10, rows2) => {
                  if (!err10 && rows2 && rows2.length) return setCover(rows2[0].image_url);
                  return setCover(null);
                }
              );
            }
          );
        } else {
          // fallback if frontend didn't send cover id
          db.query(
            'SELECT image_url FROM product_images WHERE product_id = ? ORDER BY id ASC LIMIT 1',
            [productId],
            (err10, rows2) => {
              if (!err10 && rows2 && rows2.length) return setCover(rows2[0].image_url);
              return setCover(null);
            }
          );
        }
      });
    }
  );
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
      res.json({ exists: results.length > 0 });
    }
  );
});

/* ================= DELETE PRODUCT ================= */
app.post('/admin/deleteproduct/:id', ensureAdmin, (req, res) => {
  const productId = req.params.id;

  db.query(
    'SELECT image_url FROM product_images WHERE product_id = ?',
    [productId],
    (err, images) => {
      if (err) {
        console.error('Failed to fetch product images:', err);
        return res.send('Error deleting product images');
      }

      // delete files
      images.forEach(img => {
        if (!img.image_url) return;
        const filePath = path.join(
          __dirname,
          'public',
          img.image_url.startsWith('/') ? img.image_url.slice(1) : img.image_url
        );
        fs.unlink(filePath, () => {});
      });

      // delete db rows then product
      db.query('DELETE FROM product_images WHERE product_id = ?', [productId], () => {
        db.query('DELETE FROM products WHERE id = ?', [productId], err2 => {
          if (err2) {
            console.error('Failed to delete product:', err2);
            return res.send('Failed to delete product');
          }
          res.redirect('/admin/products');
        });
      });
    }
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

  // 🔒 Duplicate check (case-insensitive)
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

      // ✅ Insert if unique
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

  // 🔒 Duplicate check (exclude itself)
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

      // ✅ Update if unique
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

app.get('/api/analytics/summary', ensureAdmin, (req, res) => {
  const sql = `
    SELECT
      (SELECT IFNULL(SUM(total_amount), 0) FROM orders) AS revenue,
      (SELECT COUNT(*) FROM orders) AS orders,
      (SELECT COUNT(*) FROM orders WHERE status = 'completed') AS fulfilled,
      (
        SELECT IFNULL(
          ROUND(
            COUNT(DISTINCT CASE WHEN cnt > 1 THEN user_id END)
            / NULLIF(COUNT(DISTINCT user_id), 0) * 100
          , 0),
        0)
        FROM (
          SELECT user_id, COUNT(*) cnt
          FROM orders
          GROUP BY user_id
        ) t
      ) AS returningRate
  `;

  db.query(sql, (err, results) => {
    if (err) return res.json({ revenue: 0, orders: 0, fulfilled: 0, returningRate: 0 });
    res.json(results[0]);
  });
});







/* ================= Live View ================= */

// Render page
app.get('/liveview', ensureAdmin, (req, res) => {
  res.render('liveview', {
    activePage: 'liveview'
  });
});


// API: REAL live stats
app.get('/api/liveview', ensureAdmin, (req, res) => {

  const sql = `
    SELECT
      (SELECT COUNT(*) FROM users) AS visitors,
      (SELECT COUNT(*) FROM orders) AS orders,
      (SELECT IFNULL(SUM(total_amount), 0) FROM orders) AS sales,
      (SELECT COUNT(*) FROM carts WHERE status = 'active') AS activeCarts,
      (SELECT COUNT(*) FROM carts WHERE status = 'checkout') AS checkingOut,
      (SELECT COUNT(*) FROM orders WHERE status = 'completed') AS purchased
  `;

  db.query(sql, (err, results) => {
    if (err) {
      console.error(err);
      return res.json({
        visitors: 0,
        orders: 0,
        sales: 0,
        activeCarts: 0,
        checkingOut: 0,
        purchased: 0
      });
    }

    res.json(results[0]);
  });
});

/* ================= Live View Map (REAL DATA) ================= */

app.get('/api/liveview/map', ensureAdmin, (req, res) => {
  const sql = `
    SELECT latitude, longitude
    FROM orders
    WHERE latitude IS NOT NULL
      AND longitude IS NOT NULL
  `;

  db.query(sql, (err, results) => {
    if (err) {
      console.error(err);
      return res.json([]);
    }
    res.json(results);
  });
});



/* ============================
   DISCOUNTS (server rendered)
   ============================ */


app.get('/discounts', ensureAdmin, (req, res) => {
  db.query(
    'SELECT * FROM discounts WHERE is_active = 1 ORDER BY created_at DESC',
    (err, results) => {
      if (err) {
        console.error(err);
        return res.status(500).send('database error');
      }

      res.render('discounts', {
        discounts: results
      });
    }
  );
});

// ============================
// add discount
// ============================

// show add page
app.get('/discounts/add', ensureAdmin, (req, res) => {
  res.render('adddiscounts');
});

// handle add
app.post('/discounts/add', ensureAdmin, (req, res) => {
  const {
    code,
    type,
    value,
    usage_limit,
    category,
    description,
    minimum_purchase,
    valid_until,
    is_active
  } = req.body;

  const sql = `
    INSERT INTO discounts
      (code, type, value, usage_limit, used,
       category, description, minimum_purchase,
       valid_until, is_active, created_at)
    VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, NOW())
  `;

  db.query(
    sql,
    [
      code,
      type,
      value,
      usage_limit || 0,
      category || null,
      description || null,
      minimum_purchase || 0,
      valid_until,
      is_active ? 1 : 0
    ],
    err => {
      if (err) {
        console.error(err);
        return res.status(500).send('database error');
      }

      res.redirect('/discounts');
    }
  );
});


// edit discount


// show edit page
app.get('/discounts/edit/:id', ensureAdmin, (req, res) => {
  db.query(
    'SELECT * FROM discounts WHERE id = ?',
    [req.params.id],
    (err, rows) => {
      if (err || rows.length === 0) {
        return res.status(404).send('discount not found');
      }

      res.render('editdiscounts', {
        discount: rows[0]
      });
    }
  );
});

// handle edit
app.post('/discounts/edit/:id', ensureAdmin, (req, res) => {
  const {
    code,
    type,
    value,
    usage_limit,
    category,
    description,
    minimum_purchase,
    valid_until,
    is_active
  } = req.body;

  const sql = `
    UPDATE discounts SET
      code = ?,
      type = ?,
      value = ?,
      usage_limit = ?,
      category = ?,
      description = ?,
      minimum_purchase = ?,
      valid_until = ?,
      is_active = ?,
      updated_at = NOW()
    WHERE id = ?
  `;

  db.query(
    sql,
    [
      code,
      type,
      value,
      usage_limit || 0,
      category || null,
      description || null,
      minimum_purchase || 0,
      valid_until,
      is_active ? 1 : 0,
      req.params.id
    ],
    err => {
      if (err) {
        console.error(err);
        return res.status(500).send('database error');
      }

      res.redirect('/discounts');
    }
  );
});

// ============================
// delete discount 
// ============================

app.post('/discounts/delete/:id', ensureAdmin, (req, res) => {
  db.query(
    'UPDATE discounts SET is_active = 0, updated_at = NOW() WHERE id = ?',
    [req.params.id],
    err => {
      if (err) {
        console.error(err);
        return res.status(500).send('database error');
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
      c.name AS category_name
    FROM products p
    LEFT JOIN categories c
      ON p.category_id = c.id
    ORDER BY p.name
  `;

  db.query(sql, (err, products) => {
    if (err) {
      console.error(err);
      return res.sendStatus(500);
    }

    // compute stats
    const stats = {
      total: products.length,
      inStock: products.filter(p => p.stock > 10).length,
      lowStock: products.filter(p => p.stock > 0 && p.stock <= 10).length,
      outOfStock: products.filter(p => p.stock === 0).length
    };

    res.render('inventory', {
      products,
      stats
    });
  });
});



app.get("/admin/inventory/update/:id", ensureAdmin, (req, res) => {
  const productId = req.params.id;

  db.query(
    "SELECT id, name, stock FROM products WHERE id = ?",
    [productId],
    (err, results) => {
      if (err || results.length === 0) {
        return res.redirect("/admin/inventory");
      }

      res.render("updateStock", { product: results[0], error: null });
    }
  );
});

app.post("/admin/inventory/update/:id", ensureAdmin, (req, res) => {
  const productId = req.params.id;
  const { stock } = req.body;

  if (stock === "" || stock === null || isNaN(stock) || Number(stock) < 0) {
    return db.query(
      "SELECT id, name, stock FROM products WHERE id = ?",
      [productId],
      (err, results) => {
        return res.render("updateStock", {
          product: results[0],
          error: "Stock must be 0 or more"
        });
      }
    );
  }

  db.query(
    "UPDATE products SET stock = ? WHERE id = ?",
    [Number(stock), productId],
    (err) => {
      if (err) {
        console.error(err);
        return res.send("Failed to update stock");
      }

      res.redirect("/admin/inventory");
    }
  );
});



// ================= USERS PAGE =================
app.get('/users', ensureAdmin, (req, res) => {
  const sql = `
    SELECT
      id,
      name,
      email,
      role,
      telegram_id,
      phone_number,
      created_at
    FROM users
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

// ================= USER DETAILS API =================
app.get('/api/users/:id', ensureAdmin, (req, res) => {
  const userId = req.params.id;

  const sql = `
    SELECT
      id,
      name,
      email,
      role,
      telegram_id,
      phone_number,
      address,
      dob,
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

// ================= SEGMENTS PAGE =================
app.get('/segments', ensureAdmin, (req, res) => {
  const sql = `
    SELECT
      'Customers with Orders' AS name,
      COUNT(DISTINCT o.user_id) AS customer_count,
      ROUND(
        COUNT(DISTINCT o.user_id) / (SELECT COUNT(*) FROM users) * 100,
        1
      ) AS percentage
    FROM orders o

    UNION ALL

    SELECT
      'Telegram-linked Customers' AS name,
      COUNT(*) AS customer_count,
      ROUND(
        COUNT(*) / (SELECT COUNT(*) FROM users) * 100,
        1
      ) AS percentage
    FROM users
    WHERE telegram_id IS NOT NULL
  `;

  db.query(sql, (err, segments) => {
    if (err) {
      console.error('Segments fetch error:', err);
      return res.render('segments', { segments: [] });
    }

    res.render('segments', { segments });
  });
});

// ================= ORDERS PAGE =================
app.get('/orders', ensureAdmin, (req, res) => {
  const sql = `
    SELECT
      id,
      full_name,
      total_amount,
      status,
      order_date
    FROM orders
    ORDER BY order_date DESC
  `;

  db.query(sql, (err, orders) => {
    if (err) {
      console.error('Orders fetch error:', err);
      return res.render('orders', { orders: [] });
    }

    res.render('orders', { orders });
  });
});

// ================= ORDER DETAILS API =================
app.get('/api/orders/:id', ensureAdmin, (req, res) => {
  const orderId = req.params.id;

  const sql = `
    SELECT
      id,
      full_name,
      email,
      address,
      phone_number,
      payment_method,
      total_amount,
      status,
      order_date
    FROM orders
    WHERE id = ?
  `;

  db.query(sql, [orderId], (err, results) => {
    if (err) {
      console.error(err);
      return res.status(500).json({ error: 'Database error' });
    }

    if (!results.length) {
      return res.status(404).json({ error: 'Order not found' });
    }

    res.json(results[0]);
  });
});


/* abandoned-checkout page */
app.get('/abandoned-checkouts', ensureAdmin, (req, res) => {
  res.render('abandoned-checkouts', { checkouts: [] });
});


/* ================= SERVER ================= */
console.log('DB HOST:', process.env.MYSQLHOST);
console.log('DB NAME:', process.env.MYSQL_DATABASE);

app.listen(3000, () => {
  console.log('Server running at http://localhost:3000');
});

module.exports = app;
