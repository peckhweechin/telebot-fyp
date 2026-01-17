const multer = require('multer');
const path = require('path');
const db = require('../db/connection');

// =======================
// MULTER CONFIG
// =======================
const storage = multer.diskStorage({
  destination: './public/uploads',
  filename: (req, file, cb) => {
    cb(null, `${Date.now()}-${file.originalname}`);
  },
});
const upload = multer({ storage: storage });
exports.upload = upload.single('image');

// =======================
// RENDER ADD PRODUCT PAGE
// =======================
exports.getAddProduct = (req, res) => {
  const categoryQuery = 'SELECT * FROM categories ORDER BY name ASC';
  db.query(categoryQuery, (err, categories) => {
    if (err) {
      console.error('Error fetching categories:', err);
      return res.status(500).send('Failed to load categories.');
    }
    res.render('addProduct', { categories });
  });
};

// =======================
// HANDLE ADD PRODUCT
// =======================
exports.postAddProduct = (req, res) => {
  console.log("🟢 Received form submission:", req.body);
  console.log("🖼️ Uploaded file:", req.file);

  const { name, price, quantity, category_id } = req.body;
  const image = req.file ? `/uploads/${req.file.filename}` : null;

  if (!name || !price || !quantity || !category_id) {
    console.error('Missing fields:', { name, price, quantity, category_id });
    return res.status(400).send('All fields are required.');
  }

  const sql = `
      INSERT INTO products (name, price, image_url, quantity, category_id)
      VALUES (?, ?, ?, ?, ?)
  `;
  db.query(sql, [name, price, image, quantity, category_id], (err) => {
    if (err) {
      console.error('❌ Error adding product:', err);
      return res.status(500).send('Failed to add product.');
    }
    console.log('✅ Product added successfully');
    res.redirect('/admin/addproduct');
  });
};

// =======================
// HANDLE ADD CATEGORY
// =======================
exports.addCategory = (req, res) => {
  const { name } = req.body;
  if (!name || !name.trim()) return res.status(400).send('Empty category name.');

  db.query('INSERT INTO categories (name) VALUES (?)', [name.trim()], (err, result) => {
    if (err) {
      console.error('❌ Error adding category:', err);
      return res.status(500).send('Failed to add category.');
    }
    console.log('✅ Category added:', name);
    res.status(200).send('OK');
  });
};

// =======================
// HANDLE DELETE CATEGORY
// =======================
exports.deleteCategory = (req, res) => {
  const { id } = req.params;

  // First, delete all products in this category
  db.query('DELETE FROM products WHERE category_id = ?', [id], (err) => {
    if (err) {
      console.error('❌ Error deleting related products:', err);
      return res.status(500).send('Failed to delete related products.');
    }

    // Then, delete the category itself
    db.query('DELETE FROM categories WHERE id = ?', [id], (err2) => {
      if (err2) {
        console.error('❌ Error deleting category:', err2);
        return res.status(500).send('Failed to delete category.');
      }

      console.log('🗑️ Category and related products deleted for ID:', id);
      res.status(200).send('OK');
    });
  });
};
