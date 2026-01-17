const express = require('express');
const mysql = require('mysql2');
const bcrypt = require('bcrypt');
const session = require('express-session');
const multer = require('multer');
const path = require('path');

const app = express();

/* ================= DATABASE ================= */
const db = mysql.createConnection({
  host: 'localhost',
  user: 'root',
  password: 'Xiaobai0409',
  database: 'telebot_fyp',
});

db.connect(err => {
  if (err) throw err;
  console.log('✅ Connected to MySQL');
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

  db.query(
    'INSERT INTO admin_users (name, email, password_hash) VALUES (?, ?, ?)',
    [username, email, hash],
    err => {
      if (err) {
        console.error(err);
        return res.render('signup', {
          error: 'Admin already exists or database error',
        });
      }
      res.redirect('/login');
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

/* ================= PRODUCT UPLOAD ================= */
const storage = multer.diskStorage({
  destination: './public/uploads/',
  filename: (req, file, cb) => {
    cb(null, Date.now() + path.extname(file.originalname));
  },
});

const upload = multer({ storage });

app.get('/admin/products', ensureAdmin, (req, res) => {
  const sql = `
    SELECT 
      p.*,
      c.name AS category_name,
      (
        SELECT pi.image_url
        FROM product_images pi
        WHERE pi.product_id = p.id
        LIMIT 1
      ) AS image_url
    FROM products p
    LEFT JOIN categories c ON p.category_id = c.id
    ORDER BY p.created_at DESC
  `;

  db.query(sql, (err, products) => {
    if (err) {
      console.error(err);
      return res.send('Failed to load products');
    }
    res.render('products', { products });
  });
});


/* add product page */
// GET Add Product
app.get('/admin/addproduct', ensureAdmin, (req, res) => {
  db.query('SELECT * FROM categories WHERE is_active = 1', (err, categories) => {
    if (err) return res.send('Failed to load categories');

    // Pass empty oldData and error by default
    res.render('addproduct', { categories, oldData: {}, error: null });
  });
});

// POST Add Product
app.post(
  '/admin/addproduct',
  ensureAdmin,
  upload.array('images', 10),
  (req, res) => {
    const { name, description, price, quantity, category_id } = req.body;

    if (!name || !price || !quantity || !category_id || !req.files || req.files.length === 0) {
      return db.query('SELECT * FROM categories WHERE is_active = 1', (err, categories) => {
        res.render('addproduct', {
          categories,
          oldData: req.body,
          error: 'All fields are required and at least one image must be uploaded'
        });
      });
    }

    // Duplicate name check
    db.query('SELECT id FROM products WHERE name = ? AND is_active = 1', [name], (err, results) => {
      if (err) {
        console.error(err);
        return db.query('SELECT * FROM categories WHERE is_active = 1', (err, categories) => {
          res.render('addproduct', { categories, oldData: req.body, error: 'Database error' });
        });
      }

      if (results.length > 0) {
        return db.query('SELECT * FROM categories WHERE is_active = 1', (err, categories) => {
          res.render('addproduct', { categories, oldData: req.body, error: 'Product with this name already exists' });
        });
      }

      // Insert product WITHOUT cover image yet
      db.query(
        `INSERT INTO products (name, description, price, quantity, category_id)
         VALUES (?, ?, ?, ?, ?)`,
        [name, description || '', price, quantity, category_id],
        (err, result) => {
          if (err) {
            console.error(err);
            return db.query('SELECT * FROM categories WHERE is_active = 1', (err, categories) => {
              res.render('addproduct', { categories, oldData: req.body, error: 'DB error' });
            });
          }

          const productId = result.insertId;

          // ✅ Set cover image based on selected coverIndex
          const coverIndex = parseInt(req.body.coverIndex) || 0;
          const coverImageUrl = '/uploads/' + req.files[coverIndex].filename;

          db.query(
            'UPDATE products SET image_url = ? WHERE id = ?',
            [coverImageUrl, productId],
            err => {
              if (err) console.error(err);

              // ✅ Insert all images into product_images table
              const imagesToInsert = req.files.map(f => [productId, '/uploads/' + f.filename]);
              db.query(
                'INSERT INTO product_images (product_id, image_url) VALUES ?',
                [imagesToInsert],
                err => {
                  if (err) console.error(err);
                  res.redirect('/admin/products');
                }
              );
            }
          );
        }
      );
    });
  }
);

/* edit product page */
app.get('/admin/editproduct/:id', ensureAdmin, async (req, res) => {
  const productId = req.params.id;

  try {
    // 1️⃣ Get product
    const [productRows] = await db.promise().query(
      'SELECT * FROM products WHERE id = ?',
      [productId]
    );

    if (productRows.length === 0) {
      return res.redirect('/admin/products');
    }

    const product = productRows[0];

    // 2️⃣ Get product images
    const [imageRows] = await db.promise().query(
      'SELECT id, image_url FROM product_images WHERE product_id = ? ORDER BY id ASC',
      [productId]
    );

    // 3️⃣ Attach images to product
    product.images = imageRows;

    // 4️⃣ Get categories
    const [categories] = await db.promise().query(
      'SELECT * FROM categories WHERE is_active = 1'
    );

    // 5️⃣ Render edit page WITH images
    res.render('editproduct', {
      product,
      categories
    });

  } catch (err) {
    console.error(err);
    res.redirect('/admin/products');
  }
});
app.post('/admin/editproduct/:id', ensureAdmin, upload.array('images', 10), (req, res) => {
  const productId = req.params.id;
  const { name, price, quantity, category_id, removedImages, coverImageId } = req.body;

  // --- 1️⃣ Update product details ---
  db.query(
    `UPDATE products SET name=?, price=?, quantity=?, category_id=? WHERE id=?`,
    [name, price, quantity, category_id, productId],
    err => {
      if (err) return res.send('DB error updating product');

      // --- 2️⃣ Remove deleted images ---
      if (removedImages) {
        const idsToRemove = removedImages.split(',').filter(Boolean);
        if (idsToRemove.length) {
          db.query(
            'DELETE FROM product_images WHERE id IN (?)',
            [idsToRemove],
            err => {
              if (err) console.error('Error deleting images:', err);
            }
          );
        }
      }

      // --- 3️⃣ Add new images ---
      if (req.files && req.files.length) {
        const newImages = req.files.map(f => [productId, '/uploads/' + f.filename]);
        db.query(
          'INSERT INTO product_images (product_id, image_url) VALUES ?',
          [newImages],
          err => {
            if (err) console.error('Error inserting new images:', err);
          }
        );
      }

      // --- 4️⃣ Update product cover ---
      let coverImageUrl = null;
      if (coverImageId) {
        db.query('SELECT image_url FROM product_images WHERE id=?', [coverImageId], (err, results) => {
          if (!err && results.length) {
            coverImageUrl = results[0].image_url;
            db.query('UPDATE products SET image_url=? WHERE id=?', [coverImageUrl, productId], err => {
              if (err) console.error('Error updating cover:', err);
              res.redirect('/admin/products');
            });
          } else {
            res.redirect('/admin/products');
          }
        });
      } else {
        res.redirect('/admin/products');
      }
    }
  );
});

// ================= CHECK DUPLICATE PRODUCT NAME =================
app.get('/admin/check-product-name', ensureAdmin, (req, res) => {
  const { name } = req.query;
  if (!name) return res.json({ exists: false });

  db.query(
    'SELECT id FROM products WHERE name = ? AND is_active = 1',
    [name],
    (err, results) => {
      if (err) {
        console.error(err);
        return res.json({ exists: false });
      }
      res.json({ exists: results.length > 0 });
    }
  );
});

// ================= CHECK DUPLICATE PRODUCT NAME ON EDIT =================
app.get('/admin/check-product-name-edit', ensureAdmin, (req, res) => {
  const { name, id } = req.query;
  if (!name) return res.json({ exists: false });

  db.query(
    'SELECT id FROM products WHERE name = ? AND id != ? AND is_active = 1',
    [name, id],
    (err, results) => {
      if (err) {
        console.error(err);
        return res.json({ exists: false });
      }
      res.json({ exists: results.length > 0 });
    }
  );
});

// DELETE PRODUCT
app.post('/admin/deleteproduct/:id', ensureAdmin, (req, res) => {
  const productId = req.params.id;

  // 1️⃣ Get all product images
  db.query('SELECT image_url FROM product_images WHERE product_id = ?', [productId], (err, images) => {
    if (err) {
      console.error('Failed to fetch product images:', err);
      return res.send('Error deleting product images');
    }

    // Delete image files
    images.forEach(img => {
      if (img.image_url) {
        const filePath = path.join(__dirname, 'public', img.image_url.startsWith('/') ? img.image_url.slice(1) : img.image_url);
        fs.unlink(filePath, err => {
          if (err) console.error('Failed to delete image file:', filePath, err);
        });
      }
    });

    // 2️⃣ Delete images from DB
    db.query('DELETE FROM product_images WHERE product_id = ?', [productId], err => {
      if (err) console.error('Failed to delete product images from DB:', err);

      // 3️⃣ Delete product
      db.query('DELETE FROM products WHERE id = ?', [productId], err => {
        if (err) {
          console.error('Failed to delete product:', err);
          return res.send('Failed to delete product');
        }

        res.redirect('/admin/products');
      });
    });
  });
});



/* ================= CATEGORIES ================= */
app.get('/admin/categories', ensureAdmin, (req, res) => {
  db.query(
    'SELECT * FROM categories WHERE is_active = 1 ORDER BY created_at DESC',
    (err, categories) => {
      if (err) {
        console.error(err);
        return res.send('Failed to load categories');
      }
      res.render('categories', { categories });
    }
  );
});
/* add category page */
app.get('/admin/categories/add', ensureAdmin, (req, res) => {
  res.render('addcategories');
});

app.post('/admin/categories/add', ensureAdmin, (req, res) => {
  const { name } = req.body;

  if (!name) {
    return res.render('addcategories', {
      error: 'Category name is required',
    });
  }

  db.query(
    'INSERT INTO categories (name) VALUES (?)',
    [name],
    err => {
      if (err) {
        console.error(err);
        return res.render('addcategories', {
          error: 'Category already exists or database error',
        });
      }

      res.redirect('/admin/categories');
    }
  );
});



/* ================= Analytics ================= */
app.get('/analytics', ensureAdmin, (req, res) => {
  res.render('analytics', {
    activePage: 'analytics'
  });
});

/* ================= Analytics API (REAL) ================= */

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
    if (err) {
      console.error(err);
      return res.json({
        revenue: 0,
        orders: 0,
        fulfilled: 0,
        returningRate: 0
      });
    }

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
   discounts (server rendered)
   ============================ */

// list all discounts
app.get('/discounts', ensureAdmin, (req, res) => {
  db.query(
    'select * from discounts order by created_at desc',
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
    insert into discounts
      (code, type, value, usage_limit, used,
       category, description, minimum_purchase,
       valid_until, is_active, created_at)
    values (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, now())
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


// ============================
// edit discount
// ============================

// show edit page
app.get('/discounts/edit/:id', ensureAdmin, (req, res) => {
  db.query(
    'select * from discounts where id = ?',
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
    update discounts set
      code = ?,
      type = ?,
      value = ?,
      usage_limit = ?,
      category = ?,
      description = ?,
      minimum_purchase = ?,
      valid_until = ?,
      is_active = ?,
      updated_at = now()
    where id = ?
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

// soft delete discount
app.post('/discounts/delete/:id', ensureAdmin, (req, res) => {
  db.query(
    'update discounts set is_active = 0, updated_at = now() where id = ?',
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



// ===== UPDATE STOCK =====
app.post("/admin/inventory/update-stock", ensureAdmin, (req, res) => {
  const { productId, newStock, reason } = req.body;

  if (!productId || newStock === undefined) {
    return res.status(400).json({ error: "Missing data" });
  }

  // 1️⃣ Get current stock
  db.query(
    "SELECT stock FROM products WHERE id = ?",
    [productId],
    (err, result) => {
      if (err || result.length === 0) {
        console.error(err);
        return res.status(500).json({ error: "Product not found" });
      }

      const oldStock = result[0].stock;
      const diff = newStock - oldStock;

      // 2️⃣ Update product stock
      db.query(
        "UPDATE products SET stock = ? WHERE id = ?",
        [newStock, productId],
        err => {
          if (err) {
            console.error(err);
            return res.status(500).json({ error: "Update failed" });
          }

          // 3️⃣ Log inventory adjustment (GOOD PRACTICE)
          db.query(
            `
            INSERT INTO inventory_adjustments
            (product_id, change_amount, reason)
            VALUES (?, ?, ?)
            `,
            [productId, diff, reason || null],
            err => {
              if (err) console.error("Inventory log error:", err);
              res.json({ success: true });
            }
          );
        }
      );
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
app.listen(3000, () => {
  console.log('Server running at http://localhost:3000');
});

module.exports = app;
