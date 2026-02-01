import os
import re
import random
import base64
import mysql.connector 
from mysql.connector import Error
import fal_client
import asyncio
import json
import logging
import hmac
import hashlib
from functools import wraps
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify, Response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from telegram.request import HTTPXRequest
from openai import OpenAI
import requests
import qrcode
from io import BytesIO

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Constants
MAX_QUANTITY = 100
MIN_QUANTITY = 1
DEFAULT_TIMEOUT = 30.0
CONNECTION_POOL_SIZE = 8

TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')

# --- DB CONFIGURATION ---
DB_HOST = os.getenv('DB_HOST', '127.0.0.1')
DB_USER = os.getenv('DB_USER', 'root')
DB_PASS = os.getenv('DB_PASSWORD', '') 
DB_NAME = os.getenv('DB_NAME', 'telebot_fyp')
DB_PORT = os.getenv('DB_PORT', 3306)

PAYPAL_CLIENT_ID = os.getenv('PAYPAL_CLIENT_ID')
PAYPAL_SECRET = os.getenv('PAYPAL_SECRET')
PAYPAL_API_BASE_URL = os.getenv('PAYPAL_API_BASE_URL')
REDIRECT_URL = os.getenv('REDIRECT_URL')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')

# HitPay Configuration
HITPAY_API_KEY = os.getenv('HITPAY_API_KEY', '')
HITPAY_SALT = os.getenv('HITPAY_SALT', '')
HITPAY_API_URL = os.getenv('HITPAY_API_URL', 'https://api.hit-pay.com/v1')

# ========== JAYDEN'S CODE: FAL Configuration for Virtual Try-On ==========
FAL_KEY = os.getenv('FAL_KEY', '')
if FAL_KEY:
    os.environ["FAL_KEY"] = FAL_KEY

# Image directories
IMAGE_DIR = os.getenv('IMAGE_DIR', 'images')
USER_IMAGE_DIR = os.getenv('USER_IMAGE_DIR', 'user_images')
os.makedirs(USER_IMAGE_DIR, exist_ok=True)
# ========== END JAYDEN'S CODE ==========

# PLACEHOLDER IMAGE 
PLACEHOLDER_IMG = "https://placehold.co/600x400.png"

# === OPENAI API ===
openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY', 'sk-proj-default'))

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-key')

# --- Telegram Globals ---
BOT_APP = None
BOT_LOOP = None
CARTS = {} 
PENDING_ORDERS = {}  # Store pending orders before payment confirmation 

# --- AI Chat Globals ---
chat_histories = {}
user_chat_contexts = {}  # Track what user talked about
user_in_chat_mode = {}   # Track if user is in chat mode
# ========== JAYDEN'S CODE: User photos storage for virtual try-on ==========
user_photos = {}         # Track user photos for virtual try-on
# ========== END JAYDEN'S CODE ==========

# ============================================================================
# DATABASE HELPERS - Core database connectivity and management
# ============================================================================
# This section handles all MySQL database operations using context managers
# for automatic connection cleanup and proper transaction handling
# ============================================================================

class DatabaseConnection:
    """Context manager for database connections
    
    PURPOSE: Ensures proper database connection lifecycle management
    - Automatically opens connections when entering 'with' block
    - Commits transactions on success
    - Rolls back transactions on errors
    - Always closes connections to prevent resource leaks
    
    USAGE:
        with DatabaseConnection() as (cur, conn):
            cur.execute("SELECT * FROM products")
            result = cur.fetchall()
        # Connection automatically committed and closed here
    """
    def __init__(self):
        self.conn = None  # Will hold the MySQL connection object
        self.cur = None   # Will hold the cursor for executing queries
    
    def __enter__(self):
        """Called when entering 'with' block - establishes database connection
        
        WHAT THIS DOES:
        1. Creates a new MySQL connection using environment variables
        2. Sets autocommit=False for manual transaction control
        3. Creates a dictionary cursor (returns rows as dicts, not tuples)
        4. Returns both cursor and connection for database operations
        
        WHY dictionary=True:
        - Returns {'id': 1, 'name': 'Product'} instead of (1, 'Product')
        - Makes code more readable: row['name'] vs row[1]
        - Prevents errors when column order changes
        """
        try:
            # Establish connection to MySQL database
            self.conn = mysql.connector.connect(
                host=DB_HOST,           # Database server address
                user=DB_USER,           # MySQL username
                password=DB_PASS,       # MySQL password
                database=DB_NAME,       # Database name to use
                port=int(DB_PORT),      # MySQL port (default 3306)
                autocommit=False,       # Manual transaction control (commit explicitly)
                connect_timeout=30,     # Wait up to 30 seconds for connection
                use_pure=True           # Use pure Python implementation (no C extension)
            )
            # Create cursor with dictionary=True for easier data access
            self.cur = self.conn.cursor(dictionary=True)
            return self.cur, self.conn
        except Error as e:
            logger.error(f"Database connection error: {e}")
            raise
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Called when exiting 'with' block - handles cleanup and transactions
        
        WHAT THIS DOES:
        1. Closes the cursor (releases query resources)
        2. If error occurred: ROLLBACK transaction (undo all changes)
        3. If no error: COMMIT transaction (save all changes)
        4. Closes database connection (releases connection from pool)
        
        TRANSACTION HANDLING:
        - exc_type is None if no error → COMMIT (save changes)
        - exc_type is set if error occurred → ROLLBACK (undo changes)
        - This ensures data integrity: either all changes succeed or none do
        
        EXAMPLE:
            with DatabaseConnection() as (cur, conn):
                cur.execute("UPDATE products SET stock=stock-1")
                cur.execute("INSERT INTO orders VALUES (...)")  # Error here!
            # Both operations ROLLED BACK - stock not decreased
        """
        if self.cur:
            self.cur.close()  # Release cursor resources
        if self.conn:
            if exc_type:
                # Error occurred - rollback to prevent partial updates
                self.conn.rollback()
                logger.error(f"Transaction rolled back due to: {exc_val}")
            else:
                # Success - commit all changes to database
                self.conn.commit()
            self.conn.close()  # Always close connection
        return False  # Don't suppress exceptions

def get_conn():
    """Legacy function for backward compatibility
    
    NOTE: This is the OLD way of getting database connections
    - Requires manual connection.close() after use
    - Risk of connection leaks if developer forgets to close
    - No automatic transaction management
    
    PREFER: Using DatabaseConnection context manager instead
    
    WHY THIS STILL EXISTS:
    - Older code sections haven't been migrated yet
    - Simpler for quick one-off queries
    - Some functions were written before context manager
    
    IMPACT:
    - Returns None if connection fails (caller must check!)
    - Caller is responsible for calling conn.close()
    - No automatic rollback on errors
    """
    try:
        # Create basic MySQL connection without context management
        conn = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASS,
            database=DB_NAME,
            port=int(DB_PORT),
            connect_timeout=30,
            use_pure=True,
            autocommit=False  # Ensure manual transaction control
        )
        return conn
    except Error as e:
        logger.error(f"Error connecting to MySQL: {e}")
        return None  # Caller MUST check for None before using!

def get_categories():
    """Retrieve all product categories from database
    
    WHAT THIS RETURNS:
    List of dictionaries: [{'id': 1, 'name': 'Electronics'}, {'id': 2, 'name': 'Clothing'}, ...]
    
    USED BY:
    - Telegram bot to display category selection menu
    
    DATABASE IMPACT:
    - Simple SELECT query (no writes)
    - Fast query (categories table is small)
    - Returns empty list [] if connection fails (graceful degradation)
    """
    conn = get_conn()  # Get database connection
    if not conn: return []  # Return empty list if DB unavailable
    cur = conn.cursor(dictionary=True)  # Dictionary cursor for {'id': X, 'name': Y}
    cur.execute("SELECT id, name FROM categories WHERE is_active = 1")  # Fetch only active categories
    rows = cur.fetchall()  # Get all results as list of dicts
    conn.close()  # IMPORTANT: Close connection to prevent leak
    return rows

def get_products(category_id=None):
    """Retrieve products, optionally filtered by category
    
    PARAMETERS:
    - category_id: If provided, only returns products in that category
                   If None, returns ALL products
    
    WHAT THIS RETURNS:
    List of product dictionaries with structure:
    [{
        'id': 1,
        'name': 'Gaming Mouse',
        'description': 'RGB gaming mouse',
        'price_cents': 4999,  # $49.99 stored as cents
        'stock': 50,
        'image_url': 'images/mouse.jpg'
    }, ...]
    
    WHY price * 100:
    - Stores prices in cents (4999 = $49.99) to avoid floating-point errors
    - Ensures exact calculations (49.99 in float can be 49.989999...)
    - CAST AS UNSIGNED converts decimal to integer cents
    
    WHY COALESCE:
    - Returns 'No description' if description column is NULL
    - Prevents None values that could break string operations
    
    DATABASE IMPACT:
    - Simple SELECT, fast for small-medium datasets
    - No indexes required for category_id filter (small table)
    """
    conn = get_conn()  # Establish connection
    if not conn: return []  # Graceful failure
    cur = conn.cursor(dictionary=True)
    
    # Build SQL query dynamically based on whether category filter is needed
    sql = """
        SELECT 
            id, 
            name, 
            COALESCE(description, 'No description') as description,  # Handle NULL descriptions
            CAST(price * 100 AS UNSIGNED) as price_cents,  # Convert dollars to cents
            stock,  # Current inventory level
            image_url
        FROM products
    """
    params = []
    if category_id:
        # Add WHERE clause only if filtering by category
        sql += " WHERE category_id = %s"
        params.append(category_id)
        
    cur.execute(sql, tuple(params))  # Execute with parameterized query (SQL injection safe)
    rows = cur.fetchall()
    conn.close()  # Clean up
    return rows

def get_product_by_id(pid):
    """Get a single product by its ID with error handling
    
    PARAMETERS:
    - pid: Product ID (integer)
    
    RETURNS:
    - Dictionary with product details if found
    - None if product doesn't exist or error occurs
    
    CRITICAL FIELDS:
    - category_id: Used to determine if product is clothing (for virtual try-on)
    - stock: Checked before allowing add-to-cart
    - price_cents: Used for cart total calculations
    
    ERROR HANDLING:
    - Returns None instead of raising exception (caller must check!)
    - Logs errors for debugging
    - Always closes connection in finally block
    
    USED BY:
    - Product detail display
    - Add to cart validation
    - Virtual try-on feature (checks category_id)
    - Order processing
    """
    conn = get_conn()
    if not conn: 
        logger.error("Failed to get database connection")
        return None  # Can't proceed without DB connection
    try:
        cur = conn.cursor(dictionary=True)
        # Fetch single product with all required fields
        cur.execute("""
            SELECT 
                id, 
                name, 
                COALESCE(description, 'No description available') as description, 
                CAST(price * 100 AS UNSIGNED) as price_cents,  # Convert to cents
                image_url,  # May be NULL or empty (legacy field)
                stock,  # Current inventory level
                category_id  # IMPORTANT: Determines if clothing (category_id=4)
            FROM products WHERE id = %s
        """, (pid,))  # Parameterized query prevents SQL injection
        row = cur.fetchone()  # Get single row or None
        logger.info(f"Retrieved product {pid}: {row['name'] if row else 'Not found'}")
        return row  # Returns dict or None
    except Error as e:
        logger.error(f"Error fetching product {pid}: {e}")
        return None  # Return None on any database error
    finally:
        conn.close()  # ALWAYS close connection, even if exception occurs

def search_products(query):
    """Search products by name or description"""
    conn = get_conn()
    if not conn: 
        logger.error("Failed to get database connection for search")
        return []
    try:
        cur = conn.cursor(dictionary=True)
        search_term = f"%{query}%"
        cur.execute("""
            SELECT 
                id, 
                name, 
                COALESCE(description, 'No description') as description, 
                CAST(price * 100 AS UNSIGNED) as price_cents, 
                stock,
                image_url
            FROM products
            WHERE name LIKE %s OR description LIKE %s
            ORDER BY name
            LIMIT 20
        """, (search_term, search_term))
        rows = cur.fetchall()
        logger.info(f"Search for '{query}' returned {len(rows)} results")
        return rows
    except Error as e:
        logger.error(f"Error searching products: {e}")
        return []
    finally:
        conn.close()

def get_or_create_user(telegram_user):
    """Get existing user ID or create new user from Telegram data
    
    PARAMETERS:
    - telegram_user: Telegram User object with .id, .full_name, .username
    
    RETURNS:
    - Internal database user ID (integer)
    - Used throughout app to track user's cart, orders, chat history
    
    WHY THIS PATTERN:
    - Telegram users don't need to "register" - auto-created on first interaction
    - Maps Telegram ID → internal database ID
    - Telegram ID is unique and stable (doesn't change)
    
    DATABASE IMPACT:
    - First time user interacts: INSERT new user record
    - Subsequent interactions: Fast SELECT by telegram_id
    - Requires COMMIT for INSERT (creates data)
    
    DUMMY EMAIL:
    - Users don't provide email, but database requires it
    - Format: {telegram_id}@telegram.fake (e.g., 12345678@telegram.fake)
    - Ensures uniqueness and prevents conflicts
    """
    conn = get_conn()
    if not conn: return None
    cur = conn.cursor(dictionary=True)
    
    # Check if user already exists by Telegram ID
    cur.execute("SELECT id FROM users WHERE telegram_id=%s", (telegram_user.id,))
    row = cur.fetchone()
    
    if row:
        # User exists - return their database ID
        uid = row['id']
    else:
        # New user - create account automatically
        dummy_email = f"{telegram_user.id}@telegram.fake"  # Unique fake email
        cur.execute(
            "INSERT INTO users (telegram_id, name, username, email) VALUES (%s, %s, %s, %s)",
            (telegram_user.id, telegram_user.full_name, telegram_user.username, dummy_email)
        )
        conn.commit()  # COMMIT required for INSERT (writes data)
        uid = cur.lastrowid  # Get auto-generated ID of new user
        
    conn.close()
    return uid  # Return internal database ID for this user

def get_user_address(user_id):
    """Get user's saved delivery address
    
    WHAT THIS DOES:
    - Retrieves the last saved delivery address for a user
    - Returns None if no address saved (first-time user)
    
    WHY SAVE ADDRESSES:
    - Improves checkout UX (users don't retype address each time)
    - Reduces input errors
    - Speeds up repeat purchases
    
    USED IN:
    - Checkout flow: Offers "Use saved address" option
    - Order creation: Pre-fills address field
    
    RETURNS:
    - String with address if saved (e.g., "123 Main St, City")
    - None if no address saved or error
    """
    conn = get_conn()
    if not conn: return None  # Can't check without DB
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT delivery_address FROM users WHERE id=%s", (user_id,))
        row = cur.fetchone()
        # Return address if exists and not NULL, otherwise None
        return row['delivery_address'] if row and row.get('delivery_address') else None
    except Error as e:
        logger.error(f"Error fetching user address: {e}")
        return None
    finally:
        conn.close()  # Always clean up

def save_user_address(user_id, address):
    """Save user's delivery address for future orders
    
    WHAT THIS CHANGES:
    - Updates the delivery_address column in users table
    - Overwrites previous address (keeps only latest)
    
    WHY UPDATE (not INSERT):
    - User record already exists (created by get_or_create_user)
    - Each user has only ONE saved address (not a history)
    - UPDATE modifies existing row, INSERT would create duplicate
    
    DATABASE IMPACT:
    - Single UPDATE query (fast)
    - Requires COMMIT to save changes permanently
    - Returns True/False so caller knows if it worked
    
    USED WHEN:
    - User enters new address during checkout
    - System asks "Save this address for next time?"
    """
    conn = get_conn()
    if not conn: return False  # Can't save without DB
    try:
        cur = conn.cursor()
        # UPDATE existing user record with new address
        cur.execute("UPDATE users SET delivery_address=%s WHERE id=%s", (address, user_id))
        conn.commit()  # COMMIT required to persist UPDATE
        logger.info(f"Saved address for user {user_id}")
        return True  # Success
    except Error as e:
        logger.error(f"Error saving user address: {e}")
        return False  # Failed
    finally:
        conn.close()

def create_order(user_id, items, total_cents, address=None, payment_method='PayPal', discount_id=None, discount_amount_cents=None):
    """Create a new order in database after payment confirmation
    
    PARAMETERS:
    - user_id: Internal database user ID (not Telegram ID!)
    - items: List of cart items [{'product_id': 1, 'qty': 2, 'unit_price_cents': 4999}, ...]
    - total_cents: Total order value in cents AFTER discount (e.g., 7998 = $79.98)
    - address: Delivery address string (optional)
    - payment_method: 'PayPal' or 'HitPay' (for tracking)
    - discount_id: ID of discount code used (None if no discount)
    - discount_amount_cents: Amount saved in cents (None if no discount)
    
    WHAT THIS CREATES:
    1. One row in 'orders' table (order header)
    2. Multiple rows in 'order_items' table (one per product)
    
    WHY TWO TABLES:
    - orders: Stores order-level data (total, address, status, discount)
    - order_items: Stores product-level data (which items, quantities)
    - This is a standard "order header/detail" pattern
    
    STATUS = 'paid':
    - This function is ONLY called after payment succeeds
    - Status starts as 'paid', later can change to 'shipped', 'delivered'
    
    DISCOUNT TRACKING:
    - discount_id: Links to discounts table (for analytics)
    - discount_amount_cents: Actual savings amount (for reporting)
    - If discount was used, also calls update_discount_usage()
    
    PHONE NUMBER:
    - Hardcoded as '00000000' (legacy field, not collected)
    - TODO: Could collect real phone numbers in future
    
    DATABASE IMPACT:
    - 1 INSERT into orders
    - N INSERTs into order_items (N = number of cart items)
    - Uses lastrowid to get generated order ID
    - Requires COMMIT (creates data)
    
    RETURNS:
    - order_id: The new order's database ID
    """
    conn = get_conn()
    cur = conn.cursor()
    
    # Get user's actual name from database (user_id is the database id, not telegram_id)
    cur.execute("SELECT name FROM users WHERE id = %s", (user_id,))
    user_row = cur.fetchone()
    full_name = user_row[0] if user_row and user_row[0] else 'Telegram User'
    
    # Create order header record (with discount info if applicable)
    sql = """
        INSERT INTO orders 
        (user_id, total_cents, address, full_name, phone_number, payment_method, status, discount_id, discount_amount_cents) 
        VALUES (%s, %s, %s, %s, '00000000', %s, 'paid', %s, %s)
    """
    cur.execute(sql, (user_id, total_cents, address, full_name, payment_method, discount_id, discount_amount_cents))
    order_id = cur.lastrowid  # Get the auto-generated order ID
    
    # Create order item records (one for each product in cart)
    for item in items:
        # Fetch fresh product data (name, price) for record-keeping
        # WHY: Product details might change later, but order records should be immutable
        cur.execute("SELECT name, price FROM products WHERE id = %s", (item['product_id'],))
        product_row = cur.fetchone()
        product_name = product_row[0] if product_row else 'Unknown Product'
        product_price = product_row[1] if product_row else 0.00
        
        # Insert order item with snapshot of product details at time of purchase
        cur.execute(
            "INSERT INTO order_items (order_id, product_id, quantity, unit_price_cents, product_name, price) VALUES (%s, %s, %s, %s, %s, %s)",
            (order_id, item['product_id'], item['qty'], item['unit_price_cents'], product_name, product_price)
        )
        
        # Decrease product stock (inventory management)
        try:
            cur.execute("""
                UPDATE products 
                SET stock = stock - %s 
                WHERE id = %s
            """, (item['qty'], item['product_id']))
            logger.info(f"Decreased stock for product {item['product_id']} by {item['qty']}")
        except Error as e:
            logger.error(f"Error decreasing stock for product {item['product_id']}: {e}")
    
    # If discount was used, increment its usage counter (in same transaction)
    if discount_id:
        try:
            cur.execute("""
                UPDATE discounts 
                SET used = used + 1 
                WHERE id = %s
            """, (discount_id,))
            logger.info(f"Order #{order_id} used discount ID {discount_id}, saved {format_price(discount_amount_cents)}")
        except Error as e:
            logger.error(f"Error updating discount usage: {e}")
        
    conn.commit()  # COMMIT all inserts, stock updates AND discount update together (atomic transaction)
    conn.close()
    return order_id  # Return new order ID for confirmation message

def get_telegram_id_for_order(order_id):
    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT u.telegram_id 
        FROM orders o 
        JOIN users u ON o.user_id = u.id 
        WHERE o.id = %s
    """, (order_id,))
    row = cur.fetchone()
    conn.close()
    return str(row['telegram_id']) if row else None

def get_latest_order(user_id):
    conn = get_conn()
    if not conn: return None
    cur = conn.cursor(dictionary=True)
    
    cur.execute("""
        SELECT id, total_cents, status, order_date as created_at 
        FROM orders 
        WHERE user_id=%s 
        ORDER BY order_date DESC 
        LIMIT 1
    """, (user_id,))
    
    order = cur.fetchone()
    
    if order:
        cur.execute("""
            SELECT oi.quantity, oi.unit_price_cents, p.name 
            FROM order_items oi 
            JOIN products p ON p.id=oi.product_id 
            WHERE oi.order_id=%s
        """, (order['id'],))
        order['items'] = cur.fetchall()
        
    conn.close()
    return order

def get_user_orders(user_id):
    conn = get_conn()
    if not conn: return []
    cur = conn.cursor(dictionary=True)
    
    cur.execute("""
        SELECT id, total_cents, status, order_date as created_at, address as delivery_address 
        FROM orders 
        WHERE user_id=%s 
        ORDER BY order_date DESC
    """, (user_id,))
    orders = cur.fetchall()
    
    for order in orders:
        cur.execute("""
            SELECT oi.quantity, oi.unit_price_cents, p.name 
            FROM order_items oi 
            JOIN products p ON p.id=oi.product_id 
            WHERE oi.order_id=%s
        """, (order['id'],))
        order['items'] = cur.fetchall()
        
    conn.close()
    return orders

# ============================================================================
# DISCOUNT CODE FUNCTIONS - Validate and apply discount codes
# ============================================================================

def get_active_discounts():
    """Get all currently active discount codes for display
    
    RETURNS:
    - List of active discount dictionaries
    - Empty list if none available or error
    
    FILTERS:
    - is_active = 1
    - valid_until >= today (or NULL for no expiration)
    - used < usage_limit (still has uses remaining)
    """
    conn = get_conn()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT code, type, value, minimum_purchase, 
                   usage_limit, used, valid_until
            FROM discounts
            WHERE is_active = 1
            AND (valid_until IS NULL OR valid_until >= CURDATE())
            AND used < usage_limit
            ORDER BY value DESC
            LIMIT 5
        """)
        discounts = cur.fetchall()
        logger.info(f"Found {len(discounts)} active discounts")
        return discounts
    except Error as e:
        logger.error(f"Error fetching active discounts: {e}")
        return []
    finally:
        conn.close()

def validate_discount_code(code):
    """Validate a discount code and return discount details if valid
    
    PARAMETERS:
    - code: Discount code string (CASE-SENSITIVE)
    
    RETURNS:
    - Dictionary with discount details if valid:
      {
          'id': 1,
          'code': 'NEWYEAR2026',
          'type': 'percentage',  # or 'fixed'
          'value': 20.00,        # percentage (20%) or fixed amount ($20)
          'minimum_purchase': 60.00,  # in dollars
          'usage_limit': 20,
          'used': 5,
          'is_active': 1,
          'valid_until': datetime object
      }
    - None if invalid (expired, inactive, usage limit reached, or doesn't exist)
    
    VALIDATION CHECKS:
    1. Code exists in database (exact match, case-sensitive)
    2. Code is active (is_active = 1)
    3. Not expired (valid_until >= today)
    4. Usage limit not reached (used < usage_limit)
    
    USED BY:
    - Checkout flow when user enters discount code
    - Shows specific error message for each failure case
    """
    conn = get_conn()
    if not conn:
        logger.error("Failed to connect to database for discount validation")
        return None
    
    try:
        cur = conn.cursor(dictionary=True)
        # Fetch discount by code (CASE-SENSITIVE)
        cur.execute("""
            SELECT id, code, type, value, minimum_purchase, 
                   usage_limit, used, is_active, valid_until, created_at
            FROM discounts
            WHERE code = %s
        """, (code,))
        
        discount = cur.fetchone()
        
        if not discount:
            logger.info(f"Discount code '{code}' not found")
            return None
        
        # Check if active
        if not discount['is_active']:
            logger.info(f"Discount code '{code}' is inactive")
            return None
        
        # Check expiration
        if discount['valid_until'] and discount['valid_until'] < datetime.now().date():
            logger.info(f"Discount code '{code}' expired on {discount['valid_until']}")
            return None
        
        # Check usage limit
        if discount['used'] >= discount['usage_limit']:
            logger.info(f"Discount code '{code}' usage limit reached ({discount['used']}/{discount['usage_limit']})")
            return None
        
        logger.info(f"Discount code '{code}' is valid: {discount['type']} - {discount['value']}")
        return discount
        
    except Error as e:
        logger.error(f"Error validating discount code: {e}")
        return None
    finally:
        conn.close()

def apply_discount(total_cents, discount):
    """Calculate discounted total based on discount type
    
    PARAMETERS:
    - total_cents: Original cart total in cents (e.g., 9999 = $99.99)
    - discount: Discount dictionary from validate_discount_code()
    
    RETURNS:
    - Dictionary with:
      {
          'original_cents': 9999,
          'discount_cents': 2000,  # Amount saved
          'final_cents': 7999,     # After discount
          'discount_description': 'NEWYEAR2026: 20% off'
      }
    - None if discount doesn't meet minimum purchase requirement
    
    DISCOUNT TYPES:
    1. 'percentage': Reduce by percentage (e.g., 20% off)
       - discount['value'] = 20 means 20% off
       - Calculation: total * (1 - 0.20)
    
    2. 'fixed': Reduce by fixed dollar amount (e.g., $10 off)
       - discount['value'] = 10.00 means $10 off
       - Calculation: total - $10
    
    MINIMUM PURCHASE:
    - If cart total < minimum_purchase, discount is NOT applied
    - Returns None so caller can show error message
    
    EDGE CASES:
    - Fixed discount larger than total: Sets final_cents to 0 (free order)
    - Percentage over 100%: Treated as 100% (free order)
    
    USED BY:
    - Checkout flow to calculate final payment amount
    - Cart display to show savings
    """
    if not discount:
        return None
    
    # Check minimum purchase requirement (convert discount min from dollars to cents)
    min_purchase_cents = int(float(discount['minimum_purchase']) * 100)
    if total_cents < min_purchase_cents:
        logger.info(f"Cart total {format_price(total_cents)} below minimum {format_price(min_purchase_cents)} for discount '{discount['code']}'")
        return None
    
    discount_type = discount['type'].lower()
    discount_value = float(discount['value'])
    
    if discount_type == 'percentage':
        # Percentage discount (e.g., 20% off)
        discount_cents = int(total_cents * (discount_value / 100))
        discount_desc = f"{discount['code']}: {discount_value}% off"
    elif discount_type == 'fixed':
        # Fixed amount discount (e.g., $10 off)
        discount_cents = int(discount_value * 100)  # Convert dollars to cents
        discount_desc = f"{discount['code']}: ${discount_value:.2f} off"
    else:
        logger.error(f"Unknown discount type: {discount_type}")
        return None
    
    # Calculate final total (don't go below 0)
    final_cents = max(0, total_cents - discount_cents)
    
    result = {
        'original_cents': total_cents,
        'discount_cents': discount_cents,
        'final_cents': final_cents,
        'discount_description': discount_desc
    }
    
    logger.info(f"Applied discount '{discount['code']}': {format_price(total_cents)} → {format_price(final_cents)} (saved {format_price(discount_cents)})")
    return result

def update_discount_usage(discount_id):
    """Increment the usage counter for a discount code after successful payment
    
    PARAMETERS:
    - discount_id: Database ID of the discount to increment
    
    WHAT THIS DOES:
    - Increments 'used' column by 1
    - Commits change to database
    - Called ONLY after payment succeeds (not on validation)
    
    WHY SEPARATE FUNCTION:
    - User might validate discount but abandon checkout
    - Only count usage after successful payment
    - Prevents users from "burning through" discount limit
    
    RETURNS:
    - True if successful
    - False if error
    
    CALLED BY:
    - create_order() function after order is confirmed
    - Payment webhook handlers after payment succeeds
    """
    conn = get_conn()
    if not conn:
        logger.error("Failed to connect to database for discount usage update")
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE discounts 
            SET used = used + 1 
            WHERE id = %s
        """, (discount_id,))
        conn.commit()
        logger.info(f"Incremented usage for discount ID {discount_id}")
        return True
    except Error as e:
        logger.error(f"Error updating discount usage: {e}")
        return False
    finally:
        conn.close()

# --- AI Chat Helpers ---
def normalize_name(name):
    """Normalize text for comparison"""
    return re.sub(r"[^\w\s]", "", name.lower())

def get_products_with_categories():
    """Get all products with their category information"""
    conn = get_conn()
    if not conn: return []
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT 
                p.id,
                p.name,
                p.description,
                CAST(p.price * 100 AS UNSIGNED) as price_cents,
                p.stock,
                p.image_url,
                c.name as category
            FROM products p
            LEFT JOIN categories c ON p.category_id = c.id
            WHERE p.stock > 0
            ORDER BY c.name, p.name
        """)
        products = cur.fetchall()
        logger.info(f"Retrieved {len(products)} products with categories")
        return products
    except Error as e:
        logger.error(f"Error fetching products with categories: {e}")
        return []
    finally:
        conn.close()

def analyze_user_intent(user_input):
    """Analyze user input to understand intent (price, category, specific product, etc.)"""
    user_lower = user_input.lower()
    intent = {
        'price_range': None,
        'keywords': [],
        'sentiment': 'neutral',
        'urgency': 'low'
    }
    
    # Extract price mentions
    import re
    price_patterns = [
        # Budget/spending patterns (must come first to avoid ambiguous "to" matching)
        (r'(?:got|have|has)\s+\$?(\d+)\s+to\s+spend', 'budget'),
        (r'budget\s+(?:of|is)?\s*\$?(\d+)', 'budget'),
        (r'\$?(\d+)\s+(?:budget|spending)', 'budget'),
        (r'can\s+spend\s+(?:up\s+to\s+)?\$?(\d+)', 'budget'),
        (r'willing\s+to\s+spend\s+(?:up\s+to\s+)?\$?(\d+)', 'budget'),
        # Under/below patterns
        (r'under\s*\$?(\d+)', 'max'),
        (r'below\s*\$?(\d+)', 'max'),
        (r'less\s+than\s*\$?(\d+)', 'max'),
        (r'max\s*\$?(\d+)', 'max'),
        # Around/about patterns
        (r'around\s*\$?(\d+)', 'max'),
        (r'about\s*\$?(\d+)', 'max'),
        # Range patterns (must be after budget patterns)
        (r'between\s*\$?(\d+)\s*and\s*\$?(\d+)', 'range'),
        (r'\$?(\d+)\s*to\s*\$?(\d+)(?!\s+spend)', 'range'),  # Negative lookahead to exclude "to spend"
    ]
    
    # Try each pattern in order (first match wins)
    for pattern, pattern_type in price_patterns:
        match = re.search(pattern, user_lower)
        if match:
            if pattern_type == 'range':
                # "$20 to $50" → extract both numbers, convert to cents
                intent['price_range'] = (int(match.group(1)) * 100, int(match.group(2)) * 100)
            else:  # budget or max
                # "under $50" or "I have $50 to spend" → 0 to max
                max_price = int(match.group(1)) * 100
                intent['price_range'] = (0, max_price)
            break  # Stop after first match
    
    # Detect urgency from keywords
    urgency_words = ['urgent', 'asap', 'quickly', 'fast', 'now', 'immediately', 'today']
    if any(word in user_lower for word in urgency_words):
        intent['urgency'] = 'high'  # User needs it fast
    
    # Detect sentiment from positive/negative words
    positive_words = ['love', 'great', 'awesome', 'perfect', 'excellent', 'amazing']
    negative_words = ['hate', 'dislike', 'boring', 'ugly', 'bad', 'worst']
    
    if any(word in user_lower for word in positive_words):
        intent['sentiment'] = 'positive'  # Happy customer
    elif any(word in user_lower for word in negative_words):
        intent['sentiment'] = 'negative'  # Unhappy or critical
    
    # Extract keywords by filtering out common/filler words
    common_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'i', 'me', 'my', 'want', 'need', 'looking', 'show', 'find'}
    words = user_lower.split()  # Split into individual words
    # Keep only meaningful words (>3 chars, not in common_words)
    intent['keywords'] = [w for w in words if len(w) > 3 and w not in common_words]
    
    return intent  # Return analyzed intent dictionary

def detect_action_intent(user_input):
    """Detect if user wants to add to cart or display a product"""
    user_lower = user_input.lower()
    
    # Add to cart patterns - use regex for flexible matching
    # These patterns allow words in between key phrases
    add_patterns_regex = [
        r'\badd\b.*\b(to|into)\b.*\bcart\b',  # "add X to cart", "add X into cart"
        r'\bput\b.*\bin\b.*\bcart\b',  # "put X in cart"
        r'\badd\b.*\b(it|that|this|one)\b',  # "add it", "add that"
        r'\bi[\s\']ll\s+take\b',  # "i'll take", "ill take"
        r'\bget\s+me\b',  # "get me X"
        r'\bgive\s+me\b',  # "give me X"
        r'\bi\s+want\b',  # "i want X"
        r'\bbuy\b',  # "buy X"
        r'\border\b',  # "order X"
        r'\bpurchase\b',  # "purchase X"
        r'\bfinali[sz]e\b.*\border\b',  # "finalize order", "finalise order"
        r'\bproceed\b',  # "proceed with that"
        r'\b(yes|yep|yeah|sure|ok|okay)\b.*\b(please|thanks|thank you)?\b$',  # affirmative responses
    ]
    
    # Display/show patterns
    display_patterns_regex = [
        r'\bshow\b.*\bme\b',  # "show me X"
        r'\blet\b.*\bme\b.*\bsee\b',  # "let me see X"
        r'\bcan\b.*\bi\b.*\bsee\b',  # "can i see X"
        r'\b(picture|image)\b.*\bof\b',  # "picture/image of X"
        r'\bwhat\b.*\bdoes\b.*\blook\s+like\b',  # "what does X look like"
        r'\bview\b.*\bthe\b',  # "view the X"
        r'\bdetails\b.*\bof\b',  # "details of X"
    ]
    
    intent = {
        'action': None,  # 'add_to_cart', 'display_product', or None
        'product_mentioned': False
    }
    
    # Check for add to cart intent with regex
    for pattern in add_patterns_regex:
        if re.search(pattern, user_lower):
            intent['action'] = 'add_to_cart'
            intent['product_mentioned'] = True
            logger.info(f"DEBUG - Detected ADD_TO_CART intent from: '{user_input}' (matched pattern: {pattern})")
            return intent
    
    # Check for display/show intent with regex
    for pattern in display_patterns_regex:
        if re.search(pattern, user_lower):
            intent['action'] = 'display_product'
            intent['product_mentioned'] = True
            logger.info(f"DEBUG - Detected DISPLAY_PRODUCT intent from: '{user_input}' (matched pattern: {pattern})")
            return intent
    
    logger.info(f"DEBUG - No specific intent detected from: '{user_input}'")
    return intent

def extract_product_from_message(user_input, all_products, chat_history=""):
    """Extract specific product name from user message using AI and conversation context"""
    user_lower = user_input.lower()
    
    # First try direct string matching for explicit product names
    for product in all_products:
        if product['name'].lower() in user_lower:
            logger.info(f"Direct match found: {product['name']} (ID: {product['id']})")
            return product
    
    # Try matching individual words/keywords from product names
    # This helps match "shorts" to "Men's Shorts" or "toy" to "Action Figure Toy"
    words_in_input = set(user_lower.split())
    best_match = None
    best_match_score = 0
    
    for product in all_products:
        product_words = set(product['name'].lower().split())
        # Count how many words match
        matching_words = words_in_input & product_words
        if matching_words:
            score = len(matching_words)
            # Bonus points if the match is a significant word (not common words)
            significant_words = matching_words - {'mens', 'womens', 'the', 'a', 'an', 'for', 'with'}
            if significant_words:
                score += len(significant_words)
                if score > best_match_score:
                    best_match = product
                    best_match_score = score
    
    if best_match and best_match_score > 0:
        logger.info(f"Keyword match found: {best_match['name']} (ID: {best_match['id']}) with score {best_match_score}")
        return best_match
    
    # Only check for pronouns if no explicit product keyword was found
    # Use word boundaries to avoid false matches like "to" in "toy"
    pronoun_patterns = [r'\bit\b', r'\bthat\b', r'\bthis\b', r'\bthe one\b', r'\bthis one\b', r'\bthat one\b']
    has_pronoun = any(re.search(pattern, user_lower) for pattern in pronoun_patterns)
    
    if has_pronoun and chat_history:
        # Use chat history to identify the referent
        logger.info(f"DEBUG - Pronoun detected, using chat history for context")
        return get_last_mentioned_product(chat_history, all_products)
    
    # If no direct match, try AI extraction as fallback
    logger.info(f"DEBUG - No keyword match found, trying AI extraction")
    return ai_extract_product(user_input, all_products, chat_history)


def ai_extract_product(user_input, all_products, chat_history=""):
    """Use AI to extract product from user message"""
    # Create a list of product names for AI matching
    product_names = [p['name'] for p in all_products]
    product_list = "\n".join([f"{i+1}. {name}" for i, name in enumerate(product_names[:50])]) # Limit to 50 for token efficiency
    
    context_info = f"\nRecent conversation:\n{chat_history[-500:]}" if chat_history else ""
    
    prompt = (
        f"The customer said: '{user_input}'\n"
        f"{context_info}\n\n"
        f"Available products:\n{product_list}\n\n"
        f"Task: Identify which specific product (if any) the customer is referring to.\n"
        f"Rules:\n"
        f"- Return ONLY the exact product name from the list above\n"
        f"- Use the conversation context to understand references like 'it', 'that', 'this'\n"
        f"- If no specific product can be identified, return 'NONE'\n"
        f"- Do not make up product names\n\n"
        f"Product name:"
    )
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a product name extractor. Return only exact product names from the given list."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=50
        )
        product_name = response.choices[0].message.content.strip()
        
        # Find matching product
        if product_name and product_name.upper() != 'NONE':
            for product in all_products:
                if product['name'].lower() == product_name.lower():
                    logger.info(f"AI extracted product: {product['name']} (ID: {product['id']})")
                    return product
        
        logger.info("No specific product extracted from message")
        return None
    except Exception as e:
        logger.error(f"Error extracting product: {e}")
        return None

def extract_multiple_products_with_quantities(user_input, all_products, chat_history=""):
    """Extract multiple products and their quantities from a single message
    Returns a list of tuples: [(product, quantity), ...]
    """
    user_lower = user_input.lower()
    found_items = []
    
    # Create a comprehensive list of products with aliases
    product_names = [p['name'] for p in all_products]
    product_list = "\n".join([f"{i+1}. {name}" for i, name in enumerate(product_names[:50])])
    
    context_info = f"\nRecent conversation:\n{chat_history[-500:]}" if chat_history else ""
    
    prompt = (
        f"The customer said: '{user_input}'\n"
        f"{context_info}\n\n"
        f"Available products:\n{product_list}\n\n"
        f"Task: Extract ALL products and their quantities from the customer's message.\n"
        f"Format your response as a list with each item on a new line:\n"
        f"ProductName1|Quantity1\n"
        f"ProductName2|Quantity2\n\n"
        f"Rules:\n"
        f"- Use ONLY exact product names from the list above\n"
        f"- Extract the quantity for each product (default to 1 if not specified)\n"
        f"- Look for phrases like '2 pairs', '1 car', '5 pens', etc.\n"
        f"- Return 'NONE' if no products can be identified\n\n"
        f"Example input: 'give me 2 pairs of shorts and 1 doraemon car'\n"
        f"Example output:\n"
        f"Men shorts|2\n"
        f"Doraemon Car|1\n"
    )
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a product and quantity extractor. Parse customer orders precisely."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=150
        )
        result = response.choices[0].message.content.strip()
        logger.info(f"AI multiple product extraction result: {result}")
        
        if result.upper() == 'NONE' or not result:
            return []
        
        # Parse the result
        lines = result.strip().split('\n')
        for line in lines:
            if '|' in line:
                parts = line.split('|')
                product_name = parts[0].strip()
                try:
                    quantity = int(parts[1].strip())
                except (ValueError, IndexError):
                    quantity = 1
                
                # Find matching product
                for product in all_products:
                    if product['name'].lower() == product_name.lower():
                        found_items.append((product, quantity))
                        logger.info(f"Extracted: {product['name']} x {quantity}")
                        break
        
        return found_items
    except Exception as e:
        logger.error(f"Error extracting multiple products: {e}")
        return []

def get_last_mentioned_product(chat_history, all_products):
    """Extract the last product mentioned in the conversation"""
    # Get recent conversation (last 3 exchanges)
    recent_history = "\n".join(chat_history.split("\n")[-6:])
    
    product_names = [p['name'] for p in all_products]
    product_list = ", ".join(product_names[:30])
    
    prompt = (
        f"Recent conversation:\n{recent_history}\n\n"
        f"Available products: {product_list}\n\n"
        f"What was the last specific product mentioned by either the agent or customer?\n"
        f"Return ONLY the exact product name, or 'NONE' if no product was mentioned."
    )
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Extract the last mentioned product name."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.1,
            max_tokens=50
        )
        product_name = response.choices[0].message.content.strip()
        
        if product_name and product_name.upper() != 'NONE':
            for product in all_products:
                if product['name'].lower() == product_name.lower():
                    return product
        
        return None
    except Exception as e:
        logger.error(f"Error getting last mentioned product: {e}")
        return None

# ========== JAYDEN'S CODE: GPT-4 Function Calling for Product Recommendations ==========
def get_sales_recommendations(user_input, chat_history, all_products, user_id=None):
    """Generate AI recommendations using GPT-4 with function calling for product recommendations"""
    intent = analyze_user_intent(user_input)
    logger.info(f"User intent analysis: {intent}")
    
    # Filter products by price range if specified
    if intent['price_range']:
        min_price, max_price = intent['price_range']
        filtered_products = [p for p in all_products if min_price <= p['price_cents'] <= max_price]
        if filtered_products:
            all_products = filtered_products
            logger.info(f"Filtered to {len(filtered_products)} products in price range ${min_price/100}-${max_price/100}")
    
    # Prepare inventory data for AI
    inventory_data = []
    for p in all_products:
        inventory_data.append({
            'id': p['id'],
            'name': p['name'],
            'price': float(p['price_cents']) / 100,
            'category': p.get('category', 'Other'),
            'stock': p.get('stock', 0)
        })
    
    inventory_json = json.dumps(inventory_data[:50])  # Limit to 50 products for token efficiency
    
    # Get cart info if available
    cart_context = ""
    if user_id and user_id in CARTS and CARTS[user_id]:
        cart_items = [item['name'] for item in CARTS[user_id]]
        cart_context = f"\nCustomer's cart: {', '.join(cart_items)}"
    
    system_prompt = (
        "You are a friendly and enthusiastic personal shopper AI. Your goal is to make shopping fun and easy!\n\n"
        "GUIDELINES:\n"
        "✨ Be warm, conversational, and encouraging\n"
        "💬 Use emojis naturally to make messages engaging\n"
        "🎯 Match products to the customer's exact needs\n"
        "📱 Keep responses concise and easy to read\n"
        "🌟 Always provide 2-5 relevant product recommendations\n"
        "💡 Give a brief, compelling reason why you recommend each product\n\n"
        "IMPORTANT: Always call the 'recommend_products' function with:\n"
        "- product_ids: Array of matching product IDs\n"
        "- sales_pitch: A friendly, engaging message (2-3 sentences) explaining your recommendations\n\n"
        f"INVENTORY: {inventory_json}\n"
        f"{cart_context}"
    )
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input}
            ],
            tools=[{
                "type": "function",
                "function": {
                    "name": "recommend_products",
                    "description": "Lists matching products with a sales pitch.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "product_ids": {"type": "array", "items": {"type": "integer"}},
                            "sales_pitch": {"type": "string"}
                        },
                        "required": ["product_ids", "sales_pitch"]
                    }
                }
            }],
            tool_choice={"type": "function", "function": {"name": "recommend_products"}}
        )
        
        # Extract function call arguments
        tool_call = response.choices[0].message.tool_calls[0]
        args = json.loads(tool_call.function.arguments)
        
        # Return the sales pitch and product IDs
        return args.get('sales_pitch', 'Check out these products!'), args.get('product_ids', [])
        
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return "I'm here to help! Could you tell me more about what you're looking for? 😊", []
# ========== END JAYDEN'S CODE ==========

def extract_relevant_products(chat_history, all_products):
    """Enhanced AI extraction of discussed products with better matching"""
    # Group by category for better context
    categories_dict = {}
    for p in all_products:
        cat = p.get('category', 'Other')
        if cat not in categories_dict:
            categories_dict[cat] = []
        categories_dict[cat].append(p['name'])
    
    categories_list = list(categories_dict.keys())
    product_by_category = "\n".join(
        [f"{cat}: {', '.join(products[:10])}" for cat, products in categories_dict.items()]
    )
    
    prompt = (
        f"Analyze this shopping conversation and identify what products the customer is interested in.\n\n"
        f"AVAILABLE CATEGORIES:\n{', '.join(categories_list)}\n\n"
        f"PRODUCTS BY CATEGORY:\n{product_by_category}\n\n"
        f"CONVERSATION:\n{chat_history}\n\n"
        f"Task: Extract the specific product names and categories the customer mentioned or showed interest in.\n"
        f"Return format: Comma-separated list of EXACT product names and category names from the lists above.\n"
        f"Example: 'Laptops, Gaming Mouse, Electronics, T-Shirts'\n"
        f"If customer hasn't mentioned specific products yet, return 'None'.\n"
        f"Only include items that the customer expressed interest in, not items you suggested."
    )
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an expert at analyzing shopping conversations and extracting customer intent. Be precise and only extract what the customer actually wants."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=150
        )
        result = response.choices[0].message.content.strip()
        logger.info(f"Extracted products context: {result}")
        return result if result.lower() != "none" else None
    except Exception as e:
        logger.error(f"Extraction Error: {e}")
        return None

# ========== JAYDEN'S CODE: Virtual Try-On with Garment Detection ==========
async def detect_clothing_type(image_path: str, product_name: str) -> str:
    """Use OpenAI Vision to detect if clothing is for tops or bottoms"""
    if not os.path.exists(image_path):
        logger.warning(f"Image not found: {image_path}")
        return "tops"  # Default to tops
    
    try:
        def encode_image(path):
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode('utf-8')
        
        # More detailed prompt to detect clothing type
        vision_response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": f"""Analyze this clothing item: {product_name}

Is this item worn on the UPPER BODY (shirt, jacket, sweater, blouse, etc.) or LOWER BODY (pants, skirt, shorts, etc.)?

Choose ONLY one of these categories:
- 'tops' for upper body clothing (shirts, jackets, blouses, sweaters, vests, dresses, swimwear on upper body)
- 'bottoms' for lower body clothing (pants, jeans, skirts, shorts, leggings)

If it's a full-body item like a dress, one-piece swimsuit, or jumpsuit, choose based on the primary focus:
- If it shows upper body primarily: 'one-pieces'
- If it shows lower body primarily: 'bottoms'

Respond with ONLY the word: 'tops' or 'bottoms' or 'one-pieces'"""},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encode_image(image_path)}"}}
            ]}],
            max_tokens=15,
            temperature=0.3
        )
        
        detected = vision_response.choices[0].message.content.lower().strip()
        # Extract valid category from response
        for category in ["tops", "bottoms", "one-pieces"]:
            if category in detected:
                logger.info(f"Detected clothing type for {product_name}: {category}")
                return category
        
        logger.warning(f"Could not detect clothing type, defaulting to 'tops'")
        return "tops"
        
    except Exception as e:
        logger.error(f"Error detecting clothing type: {e}")
        return "tops"  # Default to tops on error

async def execute_tryon(update: Update, product_id: int):
    """Execute virtual try-on with garment detection using FAL AI - NAME-BASED IMAGE SEARCH"""
    query = update.callback_query
    telegram_user_id = query.from_user.id  # For photo lookup (uses Telegram ID)
    user_id = get_or_create_user(query.from_user)  # For database and AI mode (uses DB ID)
    
    # Check if user has uploaded a photo (user_photos uses Telegram ID)
    if telegram_user_id not in user_photos:
        await query.message.reply_text(
            "🤳 **Photo Required for Virtual Try-On!**\n\n"
            "Please send a photo of yourself (any outfit is fine!).\n\n"
            "Then you can use virtual try-on for clothing items.",
            parse_mode='Markdown'
        )
        logger.info(f"User {telegram_user_id} tried try-on without uploading photo first")
        return
    
    # Get product details
    product = get_product_by_id(product_id)
    if not product:
        await query.message.reply_text("❌ Product not found!")
        return
    
    # 📂 SEARCH BY PRODUCT NAME for garment image (case-insensitive)
    product_name = product['name']
    garm_path = None
    
    # First, try exact match with different extensions
    for ext in ['.jpg', '.png', '.jpeg']:
        potential_path = os.path.join(IMAGE_DIR, f"{product_name}{ext}")
        if os.path.exists(potential_path):
            garm_path = potential_path
            break
    
    # If not found, try case-insensitive search in the directory
    if not garm_path:
        try:
            files_in_dir = os.listdir(IMAGE_DIR)
            product_name_lower = product_name.lower()
            for file in files_in_dir:
                file_name_lower = file.lower()
                # Check if filename (without extension) matches product name
                file_name_no_ext = os.path.splitext(file_name_lower)[0]
                if file_name_no_ext == product_name_lower and file_name_lower.endswith(('.jpg', '.png', '.jpeg')):
                    garm_path = os.path.join(IMAGE_DIR, file)
                    logger.info(f"Found garment image via case-insensitive search: {file}")
                    break
        except Exception as e:
            logger.error(f"Error searching for garment image: {e}")
    
    if not garm_path:
        await query.message.reply_text(f"❌ Garment image not found for '{product_name}'\n\nPlease contact support.")
        return
    
    logger.info(f"Try-on: Found garment image at {garm_path}")
    
    status_msg = await query.message.reply_text("🧠 *AI is analyzing your clothing...*", parse_mode='Markdown')
    
    try:
        # Use Vision AI to detect clothing category (tops, bottoms, one-pieces)
        await status_msg.edit_text("🔍 *Detecting clothing type (upper body or lower body)...*", parse_mode='Markdown')
        detected_category = await detect_clothing_type(garm_path, product_name)
        
        await status_msg.edit_text(f"🪄 *Detected as {detected_category}. Preparing virtual fitting...*", parse_mode='Markdown')
        
        # Upload images to FAL (user_photos uses Telegram ID)
        user_url = fal_client.upload_file(user_photos[telegram_user_id])
        garm_url = fal_client.upload_file(garm_path)
        
        logger.info(f"Executing try-on: User={user_id}, Product={product_id}, Category={detected_category}")
        
        # Execute try-on with category
        result = await fal_client.subscribe_async("fal-ai/fashn/tryon/v1.6", {
            "model_image": user_url,
            "garment_image": garm_url,
            "category": detected_category
        })
        
        # Send result with full product interface
        if result and 'images' in result and len(result['images']) > 0:
            result_image_url = result['images'][0]['url']
            
            # Build full caption matching product detail view
            stock_status = f"✅ {product['stock']} in stock" if product['stock'] > 0 else "❌ Out of Stock"
            caption = (
                f"✨ **Virtual Try-On Result** ✨\n\n"
                f"**{product_name}**\n\n"
                f"{product['description']}\n\n"
                f"💰 **Price:** {format_price(product['price_cents'])}\n"
                f"📦 **Stock:** {stock_status}\n\n"
                f"_AI detected this as: {detected_category.upper()}_"
            )
            
            # Build buttons matching product detail view
            buttons = []
            
            # Add Virtual Try-On button again (user might want to retry)
            buttons.append([InlineKeyboardButton("👗 Try Again", callback_data=f"tryon_{product_id}")])
            
            # Quantity buttons if in stock
            if product['stock'] > 0:
                buttons.append([
                    InlineKeyboardButton("➕ 1", callback_data=f"add_{product_id}_1"),
                    InlineKeyboardButton("➕ 5", callback_data=f"add_{product_id}_5"),
                    InlineKeyboardButton("➕ 10", callback_data=f"add_{product_id}_10")
                ])
                buttons.append([InlineKeyboardButton("⌨️ Custom Amount", callback_data=f"askqty_{product_id}")])
            else:
                buttons.append([InlineKeyboardButton("❌ Out of Stock", callback_data="ignore")])
            
            # Check if user is in AI chat mode - only show Continue Chat in AI mode
            in_ai_mode = user_in_chat_mode.get(user_id, False)
            logger.info(f"Virtual try-on result - User {user_id} AI mode: {in_ai_mode}")
            
            if in_ai_mode:
                # Only show Continue Chat button in AI mode
                buttons.append([InlineKeyboardButton("💬 Continue Chat", callback_data="continue_chat")])
            else:
                # Only show Back to Categories in normal browsing mode
                buttons.append([InlineKeyboardButton("🔙 Back to Categories", callback_data="categories")])
            
            await query.message.reply_photo(
                photo=result_image_url,
                caption=caption,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            logger.info(f"Virtual try-on completed successfully for user {user_id} (Telegram: {telegram_user_id}), product {product_id}")
        else:
            await query.message.reply_text("❌ Try-on completed but no image was generated. Please try again.")
        
        await status_msg.delete()
        
    except Exception as e:
        logger.error(f"TRYON ERROR: {e}", exc_info=True)
        await query.message.reply_text(f"❌ Try-on failed: {str(e)[:100]}\n\nPlease try again later or contact support.")
        try:
            await status_msg.delete()
        except:
            pass
# ========== END JAYDEN'S CODE ==========

def filter_products_by_context(context_str, all_products):
    """Enhanced product filtering with fuzzy matching and relevance scoring"""
    if not context_str:
        return all_products[:20]  # Return top 20 if no context
    
    context_normalized = normalize_name(context_str)
    context_words = set(context_normalized.split())
    
    # Score each product based on relevance
    scored_products = []
    
    for product in all_products:
        score = 0
        product_name_normalized = normalize_name(product['name'])
        product_desc_normalized = normalize_name(product.get('description', ''))
        category_normalized = normalize_name(product.get('category', '') or '')
        
        # Exact name match (highest priority)
        if product_name_normalized in context_normalized:
            score += 100
        
        # Category match
        if category_normalized and category_normalized in context_normalized:
            score += 50
        
        # Word-by-word matching
        product_words = set(product_name_normalized.split())
        matching_words = context_words.intersection(product_words)
        score += len(matching_words) * 20
        
        # Description matching (lower priority)
        desc_words = set(product_desc_normalized.split())
        desc_matches = context_words.intersection(desc_words)
        score += len(desc_matches) * 5
        
        # Partial word matching (fuzzy)
        for context_word in context_words:
            if len(context_word) > 4:  # Only for longer words
                for product_word in product_words:
                    if context_word in product_word or product_word in context_word:
                        score += 10
        
        if score > 0:
            scored_products.append((score, product))
    
    # Sort by score and return top matches
    scored_products.sort(reverse=True, key=lambda x: x[0])
    filtered = [p for score, p in scored_products]
    
    logger.info(f"Filtered {len(filtered)} products from {len(all_products)} based on context")
    
    # Return filtered products, or top 20 popular ones if no matches
    return filtered[:20] if filtered else all_products[:20]

def get_paypal_access_token():
    """Obtain OAuth access token from PayPal API
    
    WHY NEEDED:
    - PayPal API requires authentication for every request
    - Access tokens are temporary (expire after ~9 hours)
    - Must obtain fresh token using client ID + secret
    
    HOW IT WORKS:
    1. Send POST to /v1/oauth2/token with client credentials
    2. PayPal validates credentials
    3. Returns access token (JWT) for subsequent API calls
    
    SECURITY:
    - Uses HTTP Basic Auth (client_id:secret in Authorization header)
    - Credentials stored in environment variables (not hardcoded)
    - Token should be cached in production (not implemented here)
    
    WHAT THIS RETURNS:
    - String: Access token (e.g., "A21AAH...xyz")
    - None: If authentication failed
    
    USED BY:
    - Payment capture: Need token to capture PayPal orders
    - Every PayPal API call requires valid token
    """
    auth_url = f"{PAYPAL_API_BASE_URL}/v1/oauth2/token"  # OAuth endpoint
    headers = {"Accept": "application/json", "Accept-Language": "en_US"}
    auth = (PAYPAL_CLIENT_ID, PAYPAL_SECRET)  # HTTP Basic Auth tuple
    try:
        # Request access token with client_credentials grant
        response = requests.post(auth_url, headers=headers, auth=auth, data={'grant_type': 'client_credentials'})
        response.raise_for_status()  # Raise exception if HTTP error (4xx, 5xx)
        return response.json()['access_token']  # Extract token from JSON response
    except requests.exceptions.RequestException as e:
        print(f"PayPal Auth Error: {e}")
        return None  # Return None so caller can handle failure

def create_hitpay_payment(amount, currency, order_id, customer_name="Customer", customer_email="customer@example.com"):
    """Create HitPay payment request and get payment URL with QR code
    
    WHAT THIS DOES:
    1. Creates a payment request on HitPay platform
    2. Returns payment URL (user scans QR or clicks link)
    3. Sets up webhook for payment confirmation
    
    PARAMETERS:
    - amount: Decimal amount (e.g., 49.99)
    - currency: 'SGD', 'USD', etc.
    - order_id: Internal order ID for reference
    - customer_name, customer_email: For receipt
    
    RETURNS:
    Dictionary with:
    {
        'id': 'payment_request_id',
        'url': 'https://hit-pay.com/payment/...',  # Payment page URL
        'reference_number': 'ORD123',  # Our order reference
        ...
    }
    Returns None if API call fails
    
    HITPAY FLOW:
    1. Create payment request (this function)
    2. Show QR code + link to user
    3. User pays via PayNow/Card
    4. HitPay sends webhook to /hitpay_webhook
    5. Webhook creates order in database
    
    WEBHOOKS:
    - webhook: Where HitPay POSTs payment status
    - redirect_url: Where user goes after payment (success page)
    
    WHY allow_repeated_payments=false:
    - Each payment request is single-use
    - Prevents accidental duplicate charges
    """
    try:
        headers = {
            'X-BUSINESS-API-KEY': HITPAY_API_KEY,  # Authentication
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        # Build payment request payload
        payload = {
            'amount': f"{amount:.2f}",  # Format to 2 decimal places
            'currency': currency,
            'reference_number': f"ORD{order_id}",  # Prefix for identification
            'webhook': WEBHOOK_URL,  # Where HitPay sends payment status
            'redirect_url': f"{REDIRECT_URL}?order_id={order_id}",  # Success page
            'purpose': f'Order #{order_id}',  # Shown to customer
            'name': customer_name,
            'email': customer_email,
            'allow_repeated_payments': 'false'  # Single-use payment link
        }
        
        # POST to HitPay API
        response = requests.post(
            f"{HITPAY_API_URL}/payment-requests",
            headers=headers,
            data=payload  # Form-encoded data
        )
        
        # Check for success (200 or 201)
        if response.status_code == 200 or response.status_code == 201:
            result = response.json()
            logger.info(f"HitPay payment created: {result}")
            return result  # Contains payment URL and ID
        else:
            logger.error(f"HitPay API Error: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        logger.error(f"HitPay payment creation error: {e}")
        return None

def verify_hitpay_webhook(data, signature):
    """Verify HitPay webhook signature"""
    try:
        # Sort parameters and create string for HMAC
        sorted_keys = sorted(data.keys())
        params_string = ''
        for key in sorted_keys:
            if key != 'hmac':
                params_string += f"{key}{data[key]}"
        
        # Calculate HMAC
        calculated_hmac = hmac.new(
            HITPAY_SALT.encode('utf-8'),
            params_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(calculated_hmac, signature)
    except Exception as e:
        logger.error(f"Webhook verification error: {e}")
        return False

def generate_qr_code(url):
    """Generate QR code image from URL"""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Convert to bytes
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf

# ============================================================================
# FLASK ROUTES - HTTP endpoints for webhooks and payment processing
# ============================================================================
# These routes handle:
# - Payment webhooks (PayPal, HitPay)
# - Payment success/cancel pages
# - Health check endpoint
# ============================================================================

@app.route('/')
def index():
    """Health check / API info endpoint
    
    PURPOSE:
    - Verifies Flask server is running
    - Provides basic API information
    - Can be pinged by monitoring services
    
    RETURNS:
    JSON response: {"status": "ok", "note": "..."}
    
    WHEN ACCESSED:
    - Navigating to http://your-domain.com/
    - Monitoring/health check services
    - Testing if backend is alive
    """
    return jsonify({"status": "ok", "note": "Telegram Order Bot backend (MySQL)"})

@app.route('/payment_success', methods=['GET'])
def payment_success():
    # HitPay sends 'order_id', PayPal sends 'user_id' - accept both
    user_id = request.args.get('user_id') or request.args.get('order_id')
    status = request.args.get('status')
    paypal_token = request.args.get('token')  # PayPal order ID
    order_id = None  # Initialize order_id
    
    if not user_id:
        return "Missing user information."
    
    if status == 'cancelled':
        # Remove pending order
        PENDING_ORDERS.pop(int(user_id), None)
        logger.info(f"Payment cancelled by user {user_id}")
        
        return f"""
        <html>
            <head>
                <title>Payment Cancelled</title>
                <style>
                    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; 
                           text-align: center; padding: 50px; background: #f5f5f5; }}
                    .container {{ background: white; padding: 40px; border-radius: 8px; 
                                 max-width: 400px; margin: 0 auto; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                    h2 {{ color: #333; margin-bottom: 20px; }}
                    p {{ color: #666; line-height: 1.6; }}
                    .btn {{ background: #0088cc; color: white; padding: 12px 24px; 
                           text-decoration: none; border-radius: 4px; display: inline-block; 
                           margin-top: 20px; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h2>Payment Cancelled</h2>
                    <p>Your payment has been cancelled.</p>
                    <p>No charges were made.</p>
                    <a href="https://t.me/LetsOrderRpBot" class="btn">Return to Bot</a>
                </div>
            </body>
        </html>
        """
    
    # Handle PayPal payment capture
    if paypal_token:
        access_token = get_paypal_access_token()
        if not access_token:
            return "PayPal Auth Failed."

        capture_url = f"{PAYPAL_API_BASE_URL}/v2/checkout/orders/{paypal_token}/capture"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}

        try:
            response = requests.post(capture_url, headers=headers)
            response.raise_for_status()
            if response.json().get('status') == 'COMPLETED':
                # Get pending order data
                pending_order = PENDING_ORDERS.get(int(user_id))
                if not pending_order:
                    return "Order data not found. Please contact support."
                
                # Get payment method and discount info from pending order
                payment_method = pending_order.get('payment_method', 'PayPal')
                discount_id = pending_order.get('discount_id')
                discount_amount_cents = pending_order.get('discount_amount_cents')
                
                # Create order after successful payment
                order_id = create_order(
                    pending_order['user_id'],
                    pending_order['cart'],
                    pending_order['total_cents'],
                    pending_order['address'],
                    payment_method=payment_method,
                    discount_id=discount_id,
                    discount_amount_cents=discount_amount_cents
                )
                
                # Update with payment ID
                with DatabaseConnection() as (cur, conn):
                    cur.execute("UPDATE orders SET payment_id=%s WHERE id=%s", (paypal_token, order_id))
                    conn.commit()
                
                # Remove from pending orders
                PENDING_ORDERS.pop(int(user_id), None)
                
                telegram_id = get_telegram_id_for_order(order_id)
                if telegram_id and BOT_LOOP:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            send_telegram_confirmation(telegram_id, order_id), 
                            BOT_LOOP
                        )
                    except Exception as e:
                        logger.error(f"Error sending telegram confirmation: {e}")
                
                return f"""
                <html>
                    <head>
                        <title>Payment Successful</title>
                        <style>
                            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; 
                                   text-align: center; padding: 50px; background: #f5f5f5; }}
                            .container {{ background: white; padding: 40px; border-radius: 8px; 
                                         max-width: 400px; margin: 0 auto; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                            h2 {{ color: #28a745; margin-bottom: 20px; }}
                            p {{ color: #666; line-height: 1.6; }}
                            .btn {{ background: #0088cc; color: white; padding: 12px 24px; 
                                   text-decoration: none; border-radius: 4px; display: inline-block; 
                                   margin-top: 20px; }}
                        </style>
                    </head>
                    <body>
                        <div class="container">
                            <h2>Payment Successful</h2>
                            <p>Order #{order_id} confirmed</p>
                            <p>You will receive confirmation in Telegram</p>
                            <a href="https://t.me/LetsOrderRpBot" class="btn">Return to Bot</a>
                        </div>
                    </body>
                </html>
                """
            else:
                return "Payment pending or failed."
        except Exception as e:
            logger.error(f"PayPal capture error: {e}")
            return f"Error processing payment: {e}"
    
    # Handle HitPay return (check if order was already created by webhook)
    # For HitPay payments, the order is created by webhook, not here
    # So we need to look up the order by user_id to get the order_id
    if not order_id or order_id is None:
        try:
            with DatabaseConnection() as (cur, conn):
                # Find the most recent order for this user
                cur.execute("""
                    SELECT id, status 
                    FROM orders 
                    WHERE user_id = (SELECT id FROM users WHERE telegram_id = %s)
                    ORDER BY order_date DESC 
                    LIMIT 1
                """, (user_id,))
                order = cur.fetchone()
                if order:
                    order_id = order['id']
                    if order['status'] == 'paid':
                        return f"""
                        <html>
                            <head>
                                <title>Payment Confirmed</title>
                                <style>
                                    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; 
                                           text-align: center; padding: 50px; background: #f5f5f5; }}
                                    .container {{ background: white; padding: 40px; border-radius: 8px; 
                                                 max-width: 400px; margin: 0 auto; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                                    h2 {{ color: #4CAF50; margin-bottom: 20px; }}
                                    p {{ color: #666; line-height: 1.6; }}
                                    .btn {{ background: #0088cc; color: white; padding: 12px 24px; 
                                           text-decoration: none; border-radius: 4px; display: inline-block; 
                                           margin-top: 20px; }}
                                </style>
                            </head>
                            <body>
                                <div class="container">
                                    <h2>✅ Payment Confirmed!</h2>
                                    <p>Order #{order_id} has been confirmed</p>
                                    <p>You will receive confirmation in Telegram</p>
                                    <a href="https://t.me/LetsOrderRpBot" class="btn">Return to Bot</a>
                                </div>
                            </body>
                        </html>
                        """
        except Exception as e:
            logger.error(f"Database error looking up order: {e}")
    
    # Display processing message (webhook will create order soon)
    display_text = f"Order #{order_id}" if order_id else "Your order"
    
    return f"""
    <html>
        <head>
            <title>Payment Processing</title>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; 
                       text-align: center; padding: 50px; background: #f5f5f5; }}
                .container {{ background: white; padding: 40px; border-radius: 8px; 
                             max-width: 400px; margin: 0 auto; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h2 {{ color: #333; margin-bottom: 20px; }}
                p {{ color: #666; line-height: 1.6; }}
                .btn {{ background: #0088cc; color: white; padding: 12px 24px; 
                       text-decoration: none; border-radius: 4px; display: inline-block; 
                       margin-top: 20px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h2>Thank You</h2>
                <p>{display_text} payment is being processed</p>
                <p>You will receive confirmation in Telegram shortly</p>
                <a href="https://t.me/LetsOrderRpBot" class="btn">Return to Bot</a>
            </div>
        </body>
    </html>
    """

@app.route('/hitpay_webhook', methods=['POST'])
def hitpay_webhook():
    """Handle HitPay webhook for payment confirmation"""
    try:
        # Get data
        if request.content_type == 'application/json':
            data = request.json
        else:
            data = request.form.to_dict()
        
        logger.info(f"HitPay Webhook received: {data}")
        
        # Verify signature
        signature = data.get('hmac') or request.headers.get('X-SIGNATURE')
        if not signature or not verify_hitpay_webhook(data, signature):
            logger.warning("Invalid webhook signature")
            return jsonify({"error": "Invalid signature"}), 401
        
        # Extract info
        reference = data.get('reference_number', '')
        payment_status = data.get('status', '').lower()
        payment_id = data.get('payment_id')
        
        # Extract user_id from reference (format: ORD<user_id>)
        user_id = reference.replace('ORD', '') if reference.startswith('ORD') else None
        
        if not user_id:
            logger.error(f"Could not extract user ID from reference: {reference}")
            return jsonify({"error": "Invalid reference"}), 400
        
        user_id = int(user_id)
        
        # Create order if payment completed
        if payment_status == 'completed':
            # Get pending order data
            pending_order = PENDING_ORDERS.get(user_id)
            if not pending_order:
                logger.error(f"No pending order found for user {user_id}")
                return jsonify({"error": "Order data not found"}), 404
            
            # Get payment method and discount info from pending order
            payment_method = pending_order.get('payment_method', 'HitPay')
            discount_id = pending_order.get('discount_id')
            discount_amount_cents = pending_order.get('discount_amount_cents')
            logger.info(f"Using payment method: {payment_method}")
            
            # Create order after successful payment
            order_id = create_order(
                pending_order['user_id'],
                pending_order['cart'],
                pending_order['total_cents'],
                pending_order['address'],
                payment_method=payment_method,
                discount_id=discount_id,
                discount_amount_cents=discount_amount_cents
            )
            
            # Update with payment ID
            with DatabaseConnection() as (cur, conn):
                cur.execute(
                    "UPDATE orders SET payment_id=%s WHERE id=%s",
                    (payment_id, order_id)
                )
                conn.commit()
                
                logger.info(f"Order #{order_id} created and marked as paid (Payment ID: {payment_id})")
            
            # Remove from pending orders
            PENDING_ORDERS.pop(user_id, None)
            
            # Send Telegram notification
            telegram_id = get_telegram_id_for_order(order_id)
            if telegram_id and BOT_LOOP:
                try:
                    asyncio.run_coroutine_threadsafe(
                        send_telegram_confirmation(telegram_id, order_id),
                        BOT_LOOP
                    )
                except Exception as e:
                    logger.error(f"Error sending telegram confirmation: {e}")
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        return jsonify({"error": str(e)}), 500

# ============================================================================
# TELEGRAM BOT FUNCTIONS - Handlers for bot commands and interactions
# ============================================================================

def format_price(cents):
    """Convert price in cents to formatted dollar string
    
    WHY CENTS:
    - All prices stored as integers (4999 = $49.99)
    - Avoids floating-point precision issues
    - Ensures accurate calculations
    
    WHAT THIS DOES:
    - Divides by 100 to get dollars
    - Formats to exactly 2 decimal places
    - Adds dollar sign
    
    EXAMPLES:
    - format_price(4999) → "$49.99"
    - format_price(100) → "$1.00"
    - format_price(50) → "$0.50"
    
    USED EVERYWHERE:
    - Product displays
    - Cart summaries
    - Order confirmations
    - Payment amounts
    """
    return f"${cents/100:.2f}"  # Divide by 100, format with 2 decimals

async def send_telegram_confirmation(chat_id, order_id):
    if BOT_APP and BOT_APP.bot:
        keyboard = [ [InlineKeyboardButton("View Last Order", callback_data="last_order")], [InlineKeyboardButton("Main Menu", callback_data="start")]]
        await BOT_APP.bot.send_message(chat_id=chat_id, text=f"Payment for Order #{order_id} successful", reply_markup=InlineKeyboardMarkup(keyboard))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main menu handler - shows primary bot interface
    
    TRIGGERED BY:
    - /start command (when user first opens bot)
    - "Main Menu" button clicks (callback_data="start")
    - After completing various actions
    
    WHAT THIS DOES:
    1. Gets/creates user in database
    2. Initializes user's chat context (for AI assistant)
    3. Cleans up any pending states (awaiting_address, etc.)
    4. Shows main menu with inline keyboard
    
    UI ELEMENTS:
    - Welcome message with user's name
    - 6 main buttons:
      * Browse Categories - Traditional product browsing
      * Search - Keyword search
      * AI Assistant - Natural language shopping
      * Cart - View shopping cart (shows item count)
      * My Orders - Order history
      * Last Order - Quick view of most recent order
      * Help - Instructions
    
    CART BADGE:
    - Shows "🛒 Cart (3)" if items in cart
    - Shows "🛒 Cart" if empty
    - Updates dynamically based on CARTS[user_id]
    
    STATE MANAGEMENT:
    - Clears awaiting_address (in case user cancelled checkout)
    - Clears awaiting_custom_qty_pid (in case user cancelled quantity entry)
    - Ensures clean slate for new interactions
    
    CALLBACK vs COMMAND:
    - If update.callback_query: User clicked button (delete old message)
    - If update.message: User typed /start (send new message)
    """
    user = update.effective_user
    user_id = get_or_create_user(user)  # Get database ID for this user
    
    # Initialize chat context for AI assistant
    if user_id not in chat_histories:
        chat_histories[user_id] = ""
    if user_id not in user_chat_contexts:
        user_chat_contexts[user_id] = None
    if user_id not in user_in_chat_mode:
        user_in_chat_mode[user_id] = False
    
    # Clean up any pending states (user might have cancelled an action)
    keys_to_remove = ['awaiting_address', 'awaiting_custom_qty_pid']
    for k in keys_to_remove:
        if k in context.user_data: del context.user_data[k]

    # Build cart badge with item count
    cart_count = len(CARTS.get(user_id, []))
    cart_text = f"🛒 Cart ({cart_count})" if cart_count > 0 else "🛒 Cart"
    
    # Compose welcome message
    text = f"👋 Welcome, {user.full_name}\n\n🛍️ Your one-stop shop for everything! From electronics to fashion, toys to accessories, and more.\n\nExplore our catalog or let our AI assistant help you find exactly what you need."
    
    # Add active discounts to welcome message
    active_discounts = get_active_discounts()
    if active_discounts:
        text += "\n\n🎉 **ACTIVE DISCOUNTS** 🎉\n"
        for discount in active_discounts:
            if discount['type'].lower() == 'percentage':
                discount_value = f"{int(discount['value'])}% OFF"
            else:
                discount_value = f"${discount['value']:.2f} OFF"
            
            min_purchase = f" (Min: ${discount['minimum_purchase']:.2f})" if discount['minimum_purchase'] > 0 else ""
            remaining = discount['usage_limit'] - discount['used']
            
            text += f"\n🎟️ **{discount['code']}** - {discount_value}{min_purchase}"
            text += f"\n   └ {remaining} uses left"
            
            if discount['valid_until']:
                text += f" • Expires: {discount['valid_until'].strftime('%d %b %Y')}"
            text += "\n"
    
    # Build inline keyboard with main menu options
    keyboard = [
        [InlineKeyboardButton("📂 Browse Categories", callback_data="categories"),
         InlineKeyboardButton("🔍 Search", callback_data="search_products")],
        [InlineKeyboardButton("💬 AI Assistant", callback_data="chat_with_agent")],
        [InlineKeyboardButton(cart_text, callback_data="view_cart"),
         InlineKeyboardButton("📦 My Orders", callback_data="my_orders")],
        [InlineKeyboardButton("🆕 Last Order", callback_data="last_order"),
         InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    
    # Path to logo image
    logo_path = os.path.join('images', 'logo.jpg')
    
    if update.callback_query:
        # User clicked button - acknowledge and update message
        await update.callback_query.answer()
        try: await update.callback_query.message.delete()  # Delete old message
        except: pass  # Ignore if already deleted
        
        # Send photo with welcome message as caption
        if os.path.exists(logo_path):
            await update.callback_query.message.reply_photo(
                photo=open(logo_path, 'rb'),
                caption=text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            # Fallback to text if image not found
            await update.callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        # User typed /start command - send new message with photo
        if os.path.exists(logo_path):
            await update.message.reply_photo(
                photo=open(logo_path, 'rb'),
                caption=text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            # Fallback to text if image not found
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel command to cancel current operation"""
    user_id = get_or_create_user(update.effective_user)
    
    # Check if user is entering discount code - return to checkout
    if context.user_data.get('awaiting_discount_code'):
        del context.user_data['awaiting_discount_code']
        await update.message.reply_text("Skipped discount code.")
        
        # Create a fake callback query to reuse checkout_summary_handler
        async def fake_answer(*args, **kwargs):
            pass
        
        fake_query = type('obj', (object,), {
            'answer': fake_answer,
            'message': update.message,
            'delete': lambda: None
        })()
        fake_update = type('obj', (object,), {
            'callback_query': fake_query,
            'effective_user': update.effective_user
        })()
        await checkout_summary_handler(fake_update, context)
        logger.info(f"User {user_id} cancelled discount entry, returned to checkout")
        return
    
    # Clear all other pending states
    keys_to_remove = ['awaiting_address', 'awaiting_custom_qty_pid', 'awaiting_search']
    for k in keys_to_remove:
        if k in context.user_data:
            del context.user_data[k]
    
    # Exit AI chat mode if active
    if user_in_chat_mode.get(user_id, False):
        user_in_chat_mode[user_id] = False
        chat_histories[user_id] = ""
        user_chat_contexts[user_id] = None
        logger.info(f"User {user_id} exited AI chat mode via /cancel")
    
    await update.message.reply_text(
        "Operation cancelled.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Main Menu", callback_data="start")]])
    )
    logger.info(f"User {user_id} cancelled current action")

async def chat_with_agent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Activate chat mode with enhanced AI agent"""
    query = update.callback_query
    await query.answer()
    
    user_id = get_or_create_user(update.effective_user)
    user_in_chat_mode[user_id] = True
    chat_histories[user_id] = ""
    user_chat_contexts[user_id] = None
    
    logger.info(f"User {update.effective_user.id} activated enhanced chat mode")
    
    welcome_message = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "👋 **Hi! I'm your AI Shopping Assistant** 🤖\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "I'm here to help you find exactly what you're looking for! 🎯\n\n"
        "**📝 TRY ASKING ME:**\n"
        "• \"I need a gift under $50\" 💝\n"
        "• \"Show me headphones\" 🎧\n"
        "• \"What's trending?\" 📈\n"
        "• \"Add it to my cart\" 🛒\n\n"
        "Just describe what you want, and I'll find the perfect items for you! ✨\n\n"
        "_Type 'bye' or 'exit' to return to the main menu._"
    )
    
    # Delete the old message (which might be a photo) and send new text message
    try:
        await query.message.delete()
    except:
        pass
    
    await query.message.reply_text(
        text=welcome_message,
        parse_mode='Markdown'
    )

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message"""
    query = update.callback_query
    await query.answer()
    
    help_text = (
        "🛍️ **SHOP ASSISTANT**\n\n"
        "**SHOPPING**\n"
        "📂 Browse Categories - View products by category\n"
        "🔍 Search - Find specific products\n"
        "💬 AI Assistant - Get personalized recommendations\n\n"
        "**VIRTUAL TRY-ON** 👗✨\n"
        "1️⃣ Send a **photo of yourself** (any outfit is fine!)\n"
        "2️⃣ Browse to any **clothing item**\n"
        "3️⃣ Tap the **👗 Virtual Try-On** button\n"
        "🤖 AI will detect if it's a top or bottom\n"
        "✨ See how the clothing looks on YOU!\n\n"
        "**ORDERS**\n"
        "🛒 Cart - Review and modify items\n"
        "✅ Checkout - Complete purchase\n"
        "📦 Orders - View order history\n\n"
        "**COMMANDS**\n"
        "/start - Main menu\n"
        "/cancel - Cancel operation"
    )
    
    try:
        await query.edit_message_text(
            help_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="start")]])
        )
    except:
        await query.message.reply_text(
            help_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="start")]])
        )

async def continue_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return user to AI chat mode"""
    query = update.callback_query
    await query.answer()
    
    user_id = get_or_create_user(update.effective_user)
    
    # Ensure user is still in chat mode
    if user_id not in user_in_chat_mode:
        user_in_chat_mode[user_id] = True
    
    if user_id not in chat_histories:
        chat_histories[user_id] = ""
    
    # Send a prompt to continue chatting
    try:
        await query.edit_message_text(
            "💬 **Welcome back to chat!**\n\n"
            "What would you like to explore next? 🛍️",
            parse_mode='Markdown'
        )
    except:
        await query.message.reply_text(
            "💬 **Welcome back to chat!**\n\n"
            "What would you like to explore next? 🛍️",
            parse_mode='Markdown'
        )
    
    logger.info(f"User {user_id} continued chat after viewing product")

async def search_products_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initiate product search"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['awaiting_search'] = True
    
    try:
        await query.edit_message_text(
            "🔍 **SEARCH**\n\nEnter product name\n\n/cancel to stop",
            parse_mode='Markdown'
        )
    except:
        await query.message.reply_text(
            "🔍 **SEARCH**\n\nEnter product name\n\n/cancel to stop",
            parse_mode='Markdown'
        )

async def categories_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cats = get_categories()
    
    buttons = []
    for c in cats:
        buttons.append([InlineKeyboardButton(c['name'], callback_data=f"cat_{c['id']}")])
    
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="start")])
    
    try:
        await query.message.edit_text("📂 **CATEGORIES**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode='Markdown')
    except:
        await query.message.delete()
        await query.message.reply_text("📂 **CATEGORIES**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode='Markdown')

async def products_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    cat_id = int(query.data.split('_')[1])
    products = get_products(category_id=cat_id)
    
    buttons = []
    
    if not products:
        text = "No items found."
    else:
        text = "Tap an item to view details:"
        for p in products:
            buttons.append([InlineKeyboardButton(f"{p['name']} - {format_price(p['price_cents'])}", callback_data=f"prod_{p['id']}")])
            
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="categories"),
                    InlineKeyboardButton("🛒 Cart", callback_data="view_cart")])

    try:
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    except:
        await query.message.delete()
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

async def product_detail_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display product details with enhanced UI and virtual try-on for wearable items"""
    query = update.callback_query
    await query.answer()
    
    pid = int(query.data.split('_')[1])
    product = get_product_by_id(pid)
    user_id = get_or_create_user(update.effective_user)
    in_ai_mode = user_in_chat_mode.get(user_id, False)
    
    # Store the last viewed product for photo upload return
    context.user_data['last_viewed_product'] = pid
    
    if not product:
        await query.message.reply_text("Product not found.")
        logger.warning(f"Product {pid} not found")
        return

    stock_status = f"✅ {product['stock']} in stock" if product['stock'] > 0 else "❌ Out of Stock"
    caption = (
        f"✨ **{product['name']}** ✨\n\n"
        f"{product['description']}\n\n"
        f"💰 **Price:** {format_price(product['price_cents'])}\n"
        f"📦 **Stock:** {stock_status}"
    )
    
    buttons = []
    
    # ========== JAYDEN'S CODE: Name-Based Image Search ==========
    # 📂 SEARCH BY PRODUCT NAME instead of image_url
    # Try to find "Apple watch.jpg", "Aespa lightstick.png", etc. (case-insensitive)
    product_name = product['name']
    img_path = None
    
    # First, try exact match with different extensions
    for ext in ['.jpg', '.png', '.jpeg']:
        potential_path = os.path.join(IMAGE_DIR, f"{product_name}{ext}")
        if os.path.exists(potential_path):
            img_path = potential_path
            break
    
    # If not found, try case-insensitive search in the directory
    if not img_path:
        try:
            files_in_dir = os.listdir(IMAGE_DIR)
            product_name_lower = product_name.lower()
            for file in files_in_dir:
                file_name_lower = file.lower()
                # Check if filename (without extension) matches product name
                file_name_no_ext = os.path.splitext(file_name_lower)[0]
                if file_name_no_ext == product_name_lower and file_name_lower.endswith(('.jpg', '.png', '.jpeg')):
                    img_path = os.path.join(IMAGE_DIR, file)
                    logger.info(f"Found image via case-insensitive search: {file}")
                    break
        except Exception as e:
            logger.error(f"Error searching images directory: {e}")
    
    logger.info(f"Searching for image using name: {product_name} -> Found: {img_path}")
    logger.info(f"Product category_id: {product.get('category_id')}, In stock: {product['stock'] > 0}")
    
    # Check if product is in Clothes category and has required conditions for try-on
    # Conditions: Must be clothing category, have product image, be in stock, and FAL_KEY available
    # NOTE: Clothes category_id = 4 (verified from database)
    can_use_tryon = (
        product.get('category_id') == 4 and 
        img_path and 
        product['stock'] > 0 and 
        FAL_KEY
    )
    
    # Add virtual try-on button for clothing items
    if can_use_tryon:
        buttons.append([InlineKeyboardButton("👗 Virtual Try-On", callback_data=f"tryon_{product['id']}")])
        logger.info(f"Virtual Try-On button ADDED for clothing item: {product_name} (category_id={product.get('category_id')})")
    else:
        logger.info(f"Virtual Try-On button NOT added. Conditions: category_id={product.get('category_id')} (need 4), img_path={img_path}, stock={product['stock']}, FAL_KEY={'set' if FAL_KEY else 'NOT SET'}")
    # ========== END JAYDEN'S CODE ==========
    
    if product['stock'] > 0:
        # Quick add buttons
        buttons.append([
            InlineKeyboardButton("➕ 1", callback_data=f"add_{product['id']}_1"),
            InlineKeyboardButton("➕ 5", callback_data=f"add_{product['id']}_5"),
            InlineKeyboardButton("➕ 10", callback_data=f"add_{product['id']}_10")
        ])
        buttons.append([InlineKeyboardButton("⌨️ Custom Amount", callback_data=f"askqty_{product['id']}")])
    else:
        buttons.append([InlineKeyboardButton("❌ Out of Stock", callback_data="ignore")])
    
    # Different back button based on AI mode
    if in_ai_mode:
        buttons.append([InlineKeyboardButton("💬 Continue Chat", callback_data="continue_chat")])
    else:
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="categories")])

    try:
        await query.message.delete()
        if img_path:
            with open(img_path, 'rb') as f:
                await query.message.reply_photo(
                    photo=f,
                    caption=caption,
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
        else:
            # No image found - send text with note
            await query.message.reply_text(
                text=f"{caption}\n\n⚠️ _(No image found matching '{product_name}.jpg' in images folder)_",
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode='Markdown'
            )
        logger.info(f"Displayed product {pid} to user {update.effective_user.id}")
    except Exception as e:
        logger.error(f"Image failed to load for product {pid}: {e}")
        await query.message.reply_text(
            text=caption,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode='Markdown'
        )

async def select_quantity_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    pid = int(query.data.split('_')[1])
    
    buttons = [
        [InlineKeyboardButton("Enter Amount", callback_data=f"askqty_{pid}")],
        [InlineKeyboardButton("Cart", callback_data="view_cart"),
         InlineKeyboardButton("Back", callback_data=f"prod_{pid}")]
    ]
    
    try:
        await query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
    except:
        await query.message.edit_text("Select Quantity:", reply_markup=InlineKeyboardMarkup(buttons))

async def prompt_custom_quantity_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    pid = int(query.data.split('_')[1])
    product = get_product_by_id(pid)
    
    context.user_data['awaiting_custom_qty_pid'] = pid
    if 'awaiting_address' in context.user_data:
        del context.user_data['awaiting_address']

    await query.message.reply_text(
        f"⌨️ **QUANTITY**\n\nEnter amount for {product['name']}\n\n/cancel to stop",
        parse_mode='Markdown'
    )

async def add_to_cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add items to cart with validation"""
    query = update.callback_query
    
    try:
        user_id = get_or_create_user(update.effective_user)
        in_ai_mode = user_in_chat_mode.get(user_id, False)
        logger.info(f"DEBUG - add_to_cart_handler called. User: {user_id}, AI mode: {in_ai_mode}, Data: {query.data}")
        
        parts = query.data.split('_')
        pid = int(parts[1])
        qty_to_add = int(parts[2]) if len(parts) > 2 else 1
        
        logger.info(f"DEBUG - Parsed: pid={pid}, qty={qty_to_add}")
        
        # Validate quantity
        if qty_to_add > MAX_QUANTITY:
            await query.answer(f"⚠️ Maximum quantity is {MAX_QUANTITY}!", show_alert=True)
            return
        
        product = get_product_by_id(pid)
        if not product:
            logger.error(f"DEBUG - Product {pid} not found in database")
            await query.answer("Product not found!", show_alert=True)
            return
        
        logger.info(f"DEBUG - Found product: {product['name']}")
        
        # Check stock
        if product['stock'] < qty_to_add:
            await query.answer(f"⚠️ Only {product['stock']} items in stock!", show_alert=True)
            return
        
        await query.answer(f"✅ Added {qty_to_add} × {product['name']} to cart!") 
        logger.info(f"User {user_id} added {qty_to_add} × {product['name']} to cart (AI mode: {in_ai_mode})")
        
        if user_id not in CARTS: 
            CARTS[user_id] = []
            logger.info(f"DEBUG - Created new cart for user {user_id}")
        
        existing_item = next((item for item in CARTS[user_id] if item['product_id'] == pid), None)
        
        if existing_item:
            existing_item['qty'] += qty_to_add
            logger.info(f"DEBUG - Updated existing item qty to {existing_item['qty']}")
        else:
            CARTS[user_id].append({
                "product_id": product['id'],
                "name": product['name'],
                "qty": qty_to_add,
                "unit_price_cents": product['price_cents']
            })
            logger.info(f"DEBUG - Added new item to cart. Total items: {len(CARTS[user_id])}")
        
        logger.info(f"DEBUG - Cart state for user {user_id}: {CARTS[user_id]}")
        
        # Display product detail again with updated UI
        stock_status = "In Stock" if product['stock'] > 0 else "Out of Stock"
        caption = (
            f"**{product['name']}**\n\n"
            f"{product['description']}\n\n"
            f"Price: {format_price(product['price_cents'])}\n"
            f"Stock: {product['stock']} | {stock_status}\n\n"
            f"**{qty_to_add} added to cart**"
        )
        
        buttons = []
        if product['stock'] > 0:
            buttons.append([
                InlineKeyboardButton("➕ 1", callback_data=f"add_{product['id']}_1"),
                InlineKeyboardButton("➕ 5", callback_data=f"add_{product['id']}_5"),
                InlineKeyboardButton("➕ 10", callback_data=f"add_{product['id']}_10")
            ])
            buttons.append([InlineKeyboardButton("⌨️ Custom Amount", callback_data=f"askqty_{product['id']}")])
        
        # Different buttons based on AI mode
        if in_ai_mode:
            buttons.append([
                InlineKeyboardButton("🛒 Cart", callback_data="view_cart"),
                InlineKeyboardButton("💬 Continue Chat", callback_data="continue_chat")
            ])
            logger.info(f"DEBUG - Showing AI mode buttons")
        else:
            buttons.append([
                InlineKeyboardButton("🛒 Cart", callback_data="view_cart"),
                InlineKeyboardButton("🔙 Back", callback_data="categories")
            ])
            logger.info(f"DEBUG - Showing normal mode buttons")

        # ========== JAYDEN'S CODE: Name-Based Image Search for Add to Cart ==========
        # 📂 SEARCH BY PRODUCT NAME for image (same as product_detail_handler)
        product_name = product['name']
        img_path = None
        for ext in ['.jpg', '.png', '.jpeg']:
            potential_path = os.path.join(IMAGE_DIR, f"{product_name}{ext}")
            if os.path.exists(potential_path):
                img_path = potential_path
                break

        try:
            await query.message.delete()
            if img_path:
                with open(img_path, 'rb') as f:
                    await query.message.reply_photo(
                        photo=f,
                        caption=caption,
                        parse_mode='Markdown',
                        reply_markup=InlineKeyboardMarkup(buttons)
                    )
                logger.info(f"DEBUG - Successfully sent product photo response")
            else:
                # No image found - send text only
                await query.message.reply_text(
                    text=caption,
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode='Markdown'
                )
                logger.info(f"DEBUG - No image found for {product_name}, sent text only")
        # ========== END JAYDEN'S CODE ==========
        except Exception as e:
            logger.error(f"DEBUG - Image failed to load ({e}). Sending text only.")
            await query.message.reply_text(
                text=caption,
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode='Markdown'
            )
    except Exception as e:
        logger.error(f"ERROR in add_to_cart_handler: {e}", exc_info=True)
        try:
            await query.answer("Error adding to cart. Please try again.", show_alert=True)
        except:
            pass

async def view_cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View and manage cart contents"""
    query = update.callback_query
    await query.answer()
    user_id = get_or_create_user(update.effective_user)
    cart = CARTS.get(user_id, [])
    
    # Check if user is in AI chat mode
    in_ai_mode = user_in_chat_mode.get(user_id, False)
    
    # DEBUG: Log cart viewing
    logger.info(f"DEBUG - User {user_id} viewing cart. Cart contents: {cart}")
    logger.info(f"DEBUG - All CARTS keys: {list(CARTS.keys())}")
    logger.info(f"DEBUG - User in AI mode: {in_ai_mode}")
    
    try: await query.message.delete()
    except: pass

    if not cart:
        # Different buttons based on AI mode
        if in_ai_mode:
            empty_buttons = [
                [InlineKeyboardButton("💬 Continue Chatting", callback_data="ignore")]
            ]
        else:
            empty_buttons = [
                [InlineKeyboardButton("🛍️ Browse Products", callback_data="categories")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="start")]
            ]
        
        await query.message.reply_text(
            "🛒 **Your cart is empty**\n\nStart shopping to add items!",
            reply_markup=InlineKeyboardMarkup(empty_buttons),
            parse_mode='Markdown'
        )
        return

    text = "🛒 **Your Shopping Cart**\n\n"
    buttons = []
    total = 0
    
    for item in cart:
        item_total = item['qty'] * item['unit_price_cents']
        total += item_total
        text += f"**{item['name']}**\n"
        text += f"💰 {format_price(item['unit_price_cents'])} × {item['qty']} = {format_price(item_total)}\n\n"
        
        buttons.append([
            InlineKeyboardButton("➖", callback_data=f"dec_{item['product_id']}"),
            InlineKeyboardButton(f"📦 {item['qty']}", callback_data="ignore"),
            InlineKeyboardButton("➕", callback_data=f"inc_{item['product_id']}"),
            InlineKeyboardButton("🗑️", callback_data=f"remove_{item['product_id']}")
        ])

    text += f"━━━━━━━━━━━━━━━\n**💵 Total: {format_price(total)}**\n\n"
    text += f"📦 Items in cart: {len(cart)}"
    
    buttons.append([InlineKeyboardButton("✅ Proceed to Checkout", callback_data="checkout_summary")])
    
    # Different bottom buttons based on AI mode
    if in_ai_mode:
        buttons.append([
            InlineKeyboardButton("🗑️ Clear Cart", callback_data="clear_cart")
        ])
        buttons.append([InlineKeyboardButton("💬 Continue Chatting", callback_data="continue_chat")])
    else:
        buttons.append([
            InlineKeyboardButton("🗑️ Clear Cart", callback_data="clear_cart"),
            InlineKeyboardButton("➕ Add More", callback_data="categories")
        ])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="start")])
    
    logger.info(f"User {user_id} viewed cart with {len(cart)} items, total: {format_price(total)}")
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode='Markdown')

async def modify_cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cart modifications (increment/decrement/remove)"""
    query = update.callback_query
    parts = query.data.split('_')
    action = parts[0]
    pid = int(parts[1])
    user_id = get_or_create_user(update.effective_user)
    
    if user_id in CARTS:
        for i, item in enumerate(CARTS[user_id]):
            if item['product_id'] == pid:
                if action == 'inc':
                    item['qty'] += 1
                    await query.answer(f"Increased {item['name']} to {item['qty']}")
                    logger.info(f"User {user_id} increased {item['name']} to {item['qty']}")
                elif action == 'dec':
                    item['qty'] -= 1
                    if item['qty'] <= 0:
                        removed_name = item['name']
                        CARTS[user_id].pop(i)
                        await query.answer(f"🗑️ Removed {removed_name} from cart")
                        logger.info(f"User {user_id} removed {removed_name} from cart")
                    else:
                        await query.answer(f"Decreased {item['name']} to {item['qty']}")
                        logger.info(f"User {user_id} decreased {item['name']} to {item['qty']}")
                elif action == 'remove':
                    removed_name = item['name']
                    CARTS[user_id].pop(i)
                    await query.answer(f"🗑️ Removed {removed_name} from cart")
                    logger.info(f"User {user_id} removed {removed_name} from cart")
                break
    await view_cart_handler(update, context)

async def clear_cart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all items from cart"""
    query = update.callback_query
    user_id = get_or_create_user(update.effective_user)
    
    if user_id in CARTS and CARTS[user_id]:
        item_count = len(CARTS[user_id])
        CARTS[user_id] = []
        await query.answer(f"🗑️ Cleared {item_count} items from cart")
        logger.info(f"User {user_id} cleared cart with {item_count} items")
    else:
        await query.answer("Cart is already empty!")
    
    await view_cart_handler(update, context)

async def checkout_summary_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    try: await query.message.delete()
    except: pass

    user_id = get_or_create_user(update.effective_user)
    cart = CARTS.get(user_id, [])
    
    # Exit AI chat mode when starting checkout
    if user_in_chat_mode.get(user_id, False):
        user_in_chat_mode[user_id] = False
        chat_histories[user_id] = ""
        user_chat_contexts[user_id] = None
        logger.info(f"User {user_id} exited AI mode to proceed with checkout")
    
    if not cart:
        await start(update, context)
        return

    total_cents = sum(item['unit_price_cents'] * item['qty'] for item in cart)
    
    text = "🧾 **Order Summary**\n\n"
    for item in cart:
        text += f"📦 {item['name']} (×{item['qty']}) - {format_price(item['unit_price_cents'] * item['qty'])}\n"
    
    text += f"\n━━━━━━━━━━━━━━━\n"
    
    # Check if discount is already applied
    discount_info = context.user_data.get('applied_discount')
    if discount_info:
        text += f"**💰 Subtotal: {format_price(discount_info['original_cents'])}**\n"
        text += f"**🎟️ Discount: -{format_price(discount_info['discount_cents'])}**\n"
        text += f"   ({discount_info['discount_description']})\n"
        text += f"**💵 Total: {format_price(discount_info['final_cents'])}**\n\n"
    else:
        text += f"**💰 Total: {format_price(total_cents)}**\n\n"
    
    # Store cart and total for later use
    context.user_data['cart_snapshot'] = cart
    context.user_data['total_snapshot'] = total_cents
    
    # Check if user has a saved address
    saved_address = get_user_address(user_id)
    
    if saved_address:
        # User has a saved address - offer to use it or enter new one
        text += "**DELIVERY ADDRESS**\n\nPrevious address:\n\n"
        text += f"{saved_address}\n\n"
        text += "Use this address or enter a new one?"
        
        buttons = [
            [InlineKeyboardButton("✅ Use This Address", callback_data="use_saved_address")],
            [InlineKeyboardButton("📍 Enter New Address", callback_data="enter_new_address")]
        ]
        
        # Add discount button if no discount applied yet
        if not discount_info:
            buttons.append([InlineKeyboardButton("🎟️ Apply Discount Code", callback_data="apply_discount_code")])
        else:
            buttons.append([InlineKeyboardButton("❌ Remove Discount", callback_data="remove_discount")])
        
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="view_cart")])
        
        await query.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(buttons))
    else:
        # No saved address - ask for address
        text += "**DELIVERY ADDRESS**\n\nEnter your delivery address\n\n/cancel to stop"
        
        # Add discount button if no discount applied yet
        if not discount_info:
            buttons = [
                [InlineKeyboardButton("🎟️ Apply Discount Code First", callback_data="apply_discount_code")],
                [InlineKeyboardButton("🔙 Back", callback_data="view_cart")]
            ]
            await query.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await query.message.reply_text(text, parse_mode='Markdown')
        
        context.user_data['awaiting_address'] = True

async def use_saved_address_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle using saved address"""
    query = update.callback_query
    await query.answer()
    
    user_id = get_or_create_user(update.effective_user)
    saved_address = get_user_address(user_id)
    
    if not saved_address:
        await query.message.reply_text("Address not found. Please enter your address.")
        context.user_data['awaiting_address'] = True
        return
    
    # Use the saved address
    context.user_data['delivery_address'] = saved_address
    
    cart = context.user_data.get('cart_snapshot')
    total_cents = context.user_data.get('total_snapshot')
    
    if not cart:
        await query.message.reply_text("Session expired. Please start again.")
        return
    
    # Check if discount is applied
    discount_info = context.user_data.get('applied_discount')
    display_total = discount_info['final_cents'] if discount_info else total_cents
    
    # Build confirmation message
    confirm_text = f"✅ Delivery address confirmed\n\n"
    
    if discount_info:
        confirm_text += f"💰 Subtotal: {format_price(discount_info['original_cents'])}\n"
        confirm_text += f"🎟️ Discount: -{format_price(discount_info['discount_cents'])}\n"
        confirm_text += f"   ({discount_info['discount_description']})\n"
    
    confirm_text += f"**💵 Total: {format_price(display_total)}**\n\n"
    confirm_text += "Select payment method:"
    
    # Show payment method selection
    payment_keyboard = [
        [InlineKeyboardButton("💳 PayPal", callback_data="pay_paypal")],
        [InlineKeyboardButton("📱 PayNow QR", callback_data="pay_paynow")],
        [InlineKeyboardButton("❌ Cancel", callback_data="start")]
    ]
    
    await query.message.reply_text(
        confirm_text,
        reply_markup=InlineKeyboardMarkup(payment_keyboard),
        parse_mode='Markdown'
    )

async def enter_new_address_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle entering a new address"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['awaiting_address'] = True
    
    await query.message.reply_text(
        "📍 Please type your new **Delivery Address** below:\n(Type /cancel to stop)",
        parse_mode='Markdown'
    )

async def apply_discount_code_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler to prompt user to enter a discount code"""
    query = update.callback_query
    await query.answer()
    
    context.user_data['awaiting_discount_code'] = True
    
    await query.message.reply_text(
        "🎟️ **Enter your discount code:**\n\n"
        "Type the code below, or /cancel to skip",
        parse_mode='Markdown'
    )

async def remove_discount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove applied discount code"""
    query = update.callback_query
    await query.answer("❌ Discount removed")
    
    # Clear discount from context
    context.user_data.pop('applied_discount', None)
    context.user_data.pop('discount_code', None)
    
    # Go back to checkout summary
    await checkout_summary_handler(update, context)

async def my_orders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = get_or_create_user(update.effective_user)
    orders = get_user_orders(user_id)
    
    text = "📦 Your Order History\n\n"
    buttons = []
    
    if not orders: 
        text += "No orders found."
    else:
        for o in orders:
            items = ', '.join([f"{i['name']} ×{i['quantity']}" for i in o['items']])
            date = str(o['created_at']).split(' ')[0]
            status_mark = "✓" if o['status'] == 'paid' else "..." if o['status'] == 'awaiting_payment' else "✗"
            text += f"{status_mark} Order #{o['id']} - {o['status'].upper()}\n{items}\nTotal: {format_price(o['total_cents'])} | {date}\n\n"
            
            # Add "Pay Now" button for awaiting_payment orders
            if o['status'] == 'awaiting_payment':
                buttons.append([InlineKeyboardButton(f"💳Pay Order #{o['id']}", callback_data=f"pay_order_{o['id']}")])
    
    buttons.append([InlineKeyboardButton("Back", callback_data="start")])
    
    try:
        await query.edit_message_text(
            text, 
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except:
        # If edit fails, send new message
        await query.message.reply_text(
            text, 
            reply_markup=InlineKeyboardMarkup(buttons)
        )

async def last_order_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = get_or_create_user(update.effective_user)
    order = get_latest_order(user_id)
    
    if not order:
        text = "No orders found."
        buttons = [[InlineKeyboardButton("Back", callback_data="start")]]
    else:
        items_str = ""
        for item in order['items']:
            items_str += f"{item['name']} ×{item['quantity']}\n"
            
        date = str(order['created_at']).split(' ')[0]
        status_mark = "✓" if order['status'] == 'paid' else "..." if order['status'] == 'awaiting_payment' else "✗"
        
        text = (
            f"**LATEST ORDER**\n\n"
            f"{status_mark} Order #{order['id']}\n"
            f"Date: {date}\n"
            f"Status: {order['status'].upper()}\n"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
            f"{items_str}"
            f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
            f"Total: {format_price(order['total_cents'])}"
        )
        
        buttons = []
        # Add "Pay Now" button if order is awaiting payment
        if order['status'] == 'awaiting_payment':
            buttons.append([InlineKeyboardButton(f"💳Pay Order #{order['id']}", callback_data=f"pay_order_{order['id']}")])
        buttons.append([InlineKeyboardButton("Back", callback_data="start")])

    try:
        await query.edit_message_text(
            text, 
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except:
        await query.message.reply_text(
            text, 
            reply_markup=InlineKeyboardMarkup(buttons)
        )

async def general_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all text input with improved logic - including photo uploads for try-on"""
    user_id = get_or_create_user(update.effective_user)
    
    # ========== JAYDEN'S CODE: Photo Upload for Virtual Try-On ==========
    # Check if message contains a photo (for virtual try-on)
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        photo_path = os.path.join(USER_IMAGE_DIR, f"{update.message.from_user.id}.jpg")
        await file.download_to_drive(photo_path)
        user_photos[update.message.from_user.id] = photo_path
        
        # Show confirmation
        await update.message.reply_text(
            "📸 **Photo Saved!**\n\n✨ You can now use **Virtual Try-On** for any clothing items!",
            parse_mode='Markdown'
        )
        logger.info(f"User {user_id} uploaded photo for virtual try-on at {photo_path}")
        
        # If user was viewing a product, show it again with try-on option
        last_product_id = context.user_data.get('last_viewed_product')
        if last_product_id:
            product = get_product_by_id(last_product_id)
            if product:
                stock_status = f"✅ {product['stock']} in stock" if product['stock'] > 0 else "❌ Out of Stock"
                caption = (
                    f"✨ **{product['name']}** ✨\n\n"
                    f"{product['description']}\n\n"
                    f"💰 **Price:** {format_price(product['price_cents'])}\n"
                    f"📦 **Stock:** {stock_status}"
                )
                
                # Find product image
                product_name = product['name']
                img_path = None
                for ext in ['.jpg', '.png', '.jpeg']:
                    potential_path = os.path.join(IMAGE_DIR, f"{product_name}{ext}")
                    if os.path.exists(potential_path):
                        img_path = potential_path
                        break
                
                if not img_path:
                    try:
                        files_in_dir = os.listdir(IMAGE_DIR)
                        product_name_lower = product_name.lower()
                        for file in files_in_dir:
                            if file.lower().startswith(product_name_lower.split()[0]):
                                img_path = os.path.join(IMAGE_DIR, file)
                                break
                    except:
                        pass
                
                # Check if try-on is available
                can_use_tryon = (
                    product.get('category_id') == 4 and 
                    img_path and 
                    product['stock'] > 0 and 
                    FAL_KEY
                )
                
                buttons = []
                if can_use_tryon:
                    buttons.append([InlineKeyboardButton("👗 Virtual Try-On", callback_data=f"tryon_{product['id']}")]);
                
                if product['stock'] > 0:
                    # Quick add buttons
                    buttons.append([
                        InlineKeyboardButton("➕ 1", callback_data=f"add_{product['id']}_1"),
                        InlineKeyboardButton("➕ 5", callback_data=f"add_{product['id']}_5"),
                        InlineKeyboardButton("➕ 10", callback_data=f"add_{product['id']}_10")
                    ])
                    buttons.append([InlineKeyboardButton("⌨️ Custom Amount", callback_data=f"askqty_{product['id']}")])
                
                in_ai_mode = user_in_chat_mode.get(user_id, False)
                if in_ai_mode:
                    buttons.append([InlineKeyboardButton("💬 Continue Chatting", callback_data="continue_chat")])
                else:
                    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="categories")])
                
                # Send product with image (use local file if found, otherwise use URL)
                try:
                    if img_path and os.path.exists(img_path):
                        # Send local image file
                        with open(img_path, 'rb') as photo_file:
                            await update.message.reply_photo(
                                photo=photo_file,
                                caption=caption,
                                reply_markup=InlineKeyboardMarkup(buttons),
                                parse_mode='Markdown'
                            )
                    else:
                        # Fallback to URL
                        img_url = product.get('image_url', PLACEHOLDER_IMG)
                        if not img_url or not img_url.startswith('http'):
                            img_url = PLACEHOLDER_IMG
                        
                        await update.message.reply_photo(
                            photo=img_url,
                            caption=caption,
                            reply_markup=InlineKeyboardMarkup(buttons),
                            parse_mode='Markdown'
                        )
                except Exception as e:
                    logger.error(f"Error showing product after photo upload: {e}")
                    await update.message.reply_text(
                        caption,
                        reply_markup=InlineKeyboardMarkup(buttons),
                        parse_mode='Markdown'
                    )
        
        return
    # ========== END JAYDEN'S CODE ==========
    
    msg_text = update.message.text

    # 1. HANDLE SEARCH (Priority - before AI mode)
    if context.user_data.get('awaiting_search'):
        logger.info(f"User {user_id} searching for: {msg_text}")
        results = search_products(msg_text)
        
        if not results:
            await update.message.reply_text(
                f"🔍 **SEARCH RESULTS**\n\nNo products found matching '{msg_text}'\n\nTry different keywords.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔍 Search Again", callback_data="search_products"),
                     InlineKeyboardButton("🏠 Menu", callback_data="start")]
                ])
            )
        else:
            await update.message.reply_text(
                f"🔍 **SEARCH RESULTS**\n\n{len(results)} product(s) found",
                parse_mode='Markdown'
            )
            
            buttons = []
            for p in results[:10]:  # Limit to 10 results
                buttons.append([InlineKeyboardButton(
                    f"{p['name']} - {format_price(p['price_cents'])}", 
                    callback_data=f"prod_{p['id']}"
                )])
            
            buttons.append([InlineKeyboardButton("🔍 New Search", callback_data="search_products"),
                           InlineKeyboardButton("🏠 Menu", callback_data="start")])
            
            await update.message.reply_text(
                "Select product:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        
        del context.user_data['awaiting_search']
        return

    # 2. HANDLE CUSTOM QUANTITY INPUT (Priority - before AI mode)
    if 'awaiting_custom_qty_pid' in context.user_data:
        pid = context.user_data['awaiting_custom_qty_pid']
        
        if not msg_text.isdigit():
            await update.message.reply_text("Please enter a valid number\n\n/cancel to stop")
            return
            
        qty = int(msg_text)
        if qty <= 0:
            await update.message.reply_text("Quantity must be greater than 0")
            return
            
        if user_id not in CARTS: CARTS[user_id] = []

        existing_item = next((item for item in CARTS[user_id] if item['product_id'] == pid), None)
        
        if existing_item:
            existing_item['qty'] += qty
        else:
            product = get_product_by_id(pid)
            if product:
                CARTS[user_id].append({
                    "product_id": product['id'],
                    "name": product['name'],
                    "qty": qty,
                    "unit_price_cents": product['price_cents']
                })
        
        del context.user_data['awaiting_custom_qty_pid']
        await update.message.reply_text(
            f"{qty} items added to cart",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cart", callback_data="view_cart")]])
        )
        return

    # 3. HANDLE ADDRESS INPUT (Priority - before discount code)
    if context.user_data.get('awaiting_address'):
        address = msg_text
        cart = context.user_data.get('cart_snapshot')
        total_cents = context.user_data.get('total_snapshot')
        
        if not cart: 
            await update.message.reply_text("Session expired. Please start again.")
            return
        
        # Save the address to user's profile for future use
        save_user_address(user_id, address)
        
        # Save address and ask for payment method
        context.user_data['delivery_address'] = address
        del context.user_data['awaiting_address']
        
        # Check if discount is applied
        discount_info = context.user_data.get('applied_discount')
        display_total = discount_info['final_cents'] if discount_info else total_cents
        
        # Build confirmation message
        confirm_text = "✅ Address confirmed\n\n"
        
        if discount_info:
            confirm_text += f"💰 Subtotal: {format_price(discount_info['original_cents'])}\n"
            confirm_text += f"🎟️ Discount: -{format_price(discount_info['discount_cents'])}\n"
            confirm_text += f"   ({discount_info['discount_description']})\n"
        
        confirm_text += f"**💵 Total: {format_price(display_total)}**\n\n"
        confirm_text += "Select payment method:"
        
        buttons = [
            [InlineKeyboardButton("💳 PayPal", callback_data=f"pay_paypal")],
            [InlineKeyboardButton("📱 PayNow QR", callback_data=f"pay_hitpay")],
            [InlineKeyboardButton("🔙 Back", callback_data="view_cart")]
        ]
        
        await update.message.reply_text(
            confirm_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # 4. HANDLE DISCOUNT CODE INPUT (After address)
    if context.user_data.get('awaiting_discount_code'):
        discount_code = msg_text.strip()  # Keep original case (case-sensitive)
        
        # Validate the discount code
        discount = validate_discount_code(discount_code)
        
        if not discount:
            await update.message.reply_text(
                "❌ **Invalid or expired discount code**\n\n"
                "Please check the code and try again, or /cancel to skip",
                parse_mode='Markdown'
            )
            return
        
        # Get cart total
        total_cents = context.user_data.get('total_snapshot')
        if not total_cents:
            await update.message.reply_text("Session expired. Please start again.")
            del context.user_data['awaiting_discount_code']
            return
        
        # Apply the discount
        discount_result = apply_discount(total_cents, discount)
        
        if not discount_result:
            # Discount doesn't meet minimum purchase requirement
            min_purchase = float(discount['minimum_purchase'])
            await update.message.reply_text(
                f"⚠️ **Minimum purchase not met**\n\n"
                f"This discount requires a minimum purchase of ${min_purchase:.2f}\n"
                f"Your cart total: {format_price(total_cents)}\n\n"
                "/cancel to skip discount",
                parse_mode='Markdown'
            )
            return
        
        # Store discount info in context
        context.user_data['applied_discount'] = discount_result
        context.user_data['discount_code'] = discount
        del context.user_data['awaiting_discount_code']
        
        # Show success message
        await update.message.reply_text(
            f"✅ **Discount applied!**\n\n"
            f"{discount_result['discount_description']}\n\n"
            f"💰 Original: {format_price(discount_result['original_cents'])}\n"
            f"🎟️ Discount: -{format_price(discount_result['discount_cents'])}\n"
            f"**💵 New Total: {format_price(discount_result['final_cents'])}**",
            parse_mode='Markdown'
        )
        
        # Return to checkout summary to show updated total and continue with checkout
        await asyncio.sleep(1)
        
        # Build the checkout summary message with discount applied
        user_id = get_or_create_user(update.effective_user)
        cart = context.user_data.get('cart_snapshot', [])
        
        text = "🧾 **Order Summary**\n\n"
        for item in cart:
            text += f"📦 {item['name']} (×{item['qty']}) - {format_price(item['unit_price_cents'] * item['qty'])}\n"
        
        text += f"\n━━━━━━━━━━━━━━━\n"
        text += f"**💰 Subtotal: {format_price(discount_result['original_cents'])}**\n"
        text += f"**🎟️ Discount: -{format_price(discount_result['discount_cents'])}**\n"
        text += f"   ({discount_result['discount_description']})\n"
        text += f"**💵 Total: {format_price(discount_result['final_cents'])}**\n\n"
        
        # Check if user has a saved address
        saved_address = get_user_address(user_id)
        
        if saved_address:
            text += "**DELIVERY ADDRESS**\n\nPrevious address:\n\n"
            text += f"{saved_address}\n\n"
            text += "Use this address or enter a new one?"
            
            buttons = [
                [InlineKeyboardButton("✅ Use This Address", callback_data="use_saved_address")],
                [InlineKeyboardButton("📍 Enter New Address", callback_data="enter_new_address")],
                [InlineKeyboardButton("❌ Remove Discount", callback_data="remove_discount")],
                [InlineKeyboardButton("🔙 Back", callback_data="view_cart")]
            ]
            
            await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(buttons))
        else:
            text += "**DELIVERY ADDRESS**\n\nPlease type your **Delivery Address** below:\n(Type /cancel to stop)"
            
            buttons = [
                [InlineKeyboardButton("❌ Remove Discount", callback_data="remove_discount")],
                [InlineKeyboardButton("🔙 Back", callback_data="view_cart")]
            ]
            
            await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(buttons))
            context.user_data['awaiting_address'] = True
        
        return

    # 5. CHECK IF USER IS IN AI CHAT MODE
    if user_in_chat_mode.get(user_id, False):
        # === "I'm Ready" - Filter & Return to Main Page ===
        ready_keywords = ["im ready", "i'm ready", "ready", "show me", "done chatting", "done talking", "show products", "let me see"]
        if any(word in normalize_name(msg_text) for word in ready_keywords):
            if user_id in user_chat_contexts and user_chat_contexts[user_id]:
                await update.message.reply_text("Finding products based on our conversation...")
                
                # Use enhanced products function with categories
                all_products = get_products_with_categories()
                context_str = user_chat_contexts[user_id]
                filtered_products = filter_products_by_context(context_str, all_products)
                
                if not filtered_products:
                    await update.message.reply_text(
                        "No exact matches found. Showing popular items instead.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Browse", callback_data="categories")]])
                    )
                    user_in_chat_mode[user_id] = False
                    return
                
                # Get unique categories from filtered products
                categories = list(set(p.get('category', '') for p in filtered_products if p.get('category')))
                
                response = f"**RECOMMENDATIONS**\n\n{len(filtered_products)} items found"
                if categories:
                    response += f"\n\nCategories: {', '.join(categories)}"
                
                await update.message.reply_text(response, parse_mode='Markdown')
                
                # Show top 10 filtered products
                shown_count = 0
                for item in filtered_products[:10]:
                    stock_status = "In Stock" if item.get('stock', 0) > 0 else "Low Stock"
                    caption = (
                        f"**{item['name']}**\n\n"
                        f"${item['price_cents']/100:.2f}\n"
                        f"{stock_status}\n"
                        f"Category: {item.get('category', 'N/A')}"
                    )
                    
                    img_url = item.get('image_url')
                    if not img_url or not img_url.startswith('http'):
                        img_url = PLACEHOLDER_IMG
                    
                    buttons = [
                        [
                            InlineKeyboardButton("+1", callback_data=f"add_{item['id']}_1"),
                            InlineKeyboardButton("+5", callback_data=f"add_{item['id']}_5")
                        ],
                        [InlineKeyboardButton("Details", callback_data=f"prod_{item['id']}")]
                    ]
                    
                    try:
                        await update.message.reply_photo(
                            photo=img_url,
                            caption=caption,
                            reply_markup=InlineKeyboardMarkup(buttons),
                            parse_mode="Markdown"
                        )
                        shown_count += 1
                        await asyncio.sleep(0.5)  # Small delay to avoid flooding
                    except Exception as e:
                        logger.error(f"Error sending product image: {e}")
                        await update.message.reply_text(
                            caption, 
                            reply_markup=InlineKeyboardMarkup(buttons),
                            parse_mode="Markdown"
                        )
                
                # Final message with options
                final_buttons = [
                    [InlineKeyboardButton("Cart", callback_data="view_cart"),
                     InlineKeyboardButton("Search", callback_data="search_products")],
                    [InlineKeyboardButton("Menu", callback_data="start")]
                ]
                await update.message.reply_text(
                    f"{shown_count} products shown. Need anything else?",
                    reply_markup=InlineKeyboardMarkup(final_buttons)
                )
                
                user_in_chat_mode[user_id] = False
                chat_histories[user_id] += "\nAgent: Showed product recommendations."
                logger.info(f"User {user_id} completed chat mode, showed {shown_count} products")
                return
            else:
                await update.message.reply_text(
                    "Tell me more about what you're looking for."
                )
                return

        # === AI Conversation in Chat Mode ===
        # Use enhanced products with categories
        all_products = get_products_with_categories()
        
        # Check if user wants to add to cart or see a product (PRIORITY - check this first)
        action_intent = detect_action_intent(msg_text)
        logger.info(f"DEBUG - Action intent detected: {action_intent} for message: '{msg_text}'")
        
        if action_intent['action'] == 'add_to_cart':
            # User wants to add something to cart
            logger.info(f"DEBUG - Attempting to extract product(s) from: '{msg_text}'")
            chat_history = chat_histories.get(user_id, "")
            
            # Check if user is confirming a previous offer (simple affirmative with no specific product)
            is_simple_confirmation = msg_text.lower().strip() in ['yes', 'yep', 'yeah', 'sure', 'ok', 'okay', 'yup', 'proceed', 'finalize', 'finalize it']
            
            if is_simple_confirmation and chat_history:
                # Extract products from the AGENT's last message in chat history
                logger.info(f"DEBUG - User confirmed with simple '{msg_text}', extracting from chat history")
                
                # Get the last agent message
                history_lines = chat_history.split('\n')
                last_agent_message = ""
                for line in reversed(history_lines):
                    if line.startswith("Agent:"):
                        last_agent_message = line.replace("Agent:", "").strip()
                        break
                
                logger.info(f"DEBUG - Last agent message: {last_agent_message}")
                
                if last_agent_message:
                    # Try to extract products from what the agent said
                    items = extract_multiple_products_with_quantities(last_agent_message, all_products, chat_history)
                    
                    if items:
                        # Add all items to cart
                        if user_id not in CARTS:
                            CARTS[user_id] = []
                            logger.info(f"Created new cart for user {user_id}")
                        
                        added_items = []
                        for product, quantity in items:
                            # Check if product already in cart
                            existing_item = next((item for item in CARTS[user_id] if item['product_id'] == product['id']), None)
                            
                            if existing_item:
                                existing_item['qty'] += quantity
                                added_items.append(f"{quantity} more {product['name']} (now {existing_item['qty']} total)")
                                logger.info(f"AI Agent: User {user_id} increased {product['name']} qty to {existing_item['qty']}")
                            else:
                                CARTS[user_id].append({
                                    "product_id": product['id'],
                                    "name": product['name'],
                                    "qty": quantity,
                                    "unit_price_cents": product['price_cents']
                                })
                                added_items.append(f"{quantity}x {product['name']}")
                                logger.info(f"AI Agent: User {user_id} added {quantity}x {product['name']} to cart")
                        
                        # DEBUG: Log the entire cart state
                        logger.info(f"DEBUG - Current CARTS state for user {user_id}: {CARTS[user_id]}")
                        
                        cart_total = sum(item['qty'] for item in CARTS[user_id])
                        cart_value = sum(item['qty'] * item['unit_price_cents'] for item in CARTS[user_id])
                        
                        items_list = "\n".join([f"✅ {item}" for item in added_items])
                        response = (
                            f"**Added to cart:**\n{items_list}\n\n"
                            f"🛒 Cart: {cart_total} item(s)\n"
                            f"💰 Total: {format_price(cart_value)}\n\n"
                            f"Anything else you'd like to add?"
                        )
                        
                        buttons = [
                            [InlineKeyboardButton("🛒 View Cart", callback_data="view_cart")],
                            [InlineKeyboardButton("✅ Checkout", callback_data="checkout_summary")],
                            [InlineKeyboardButton("💬 Keep Shopping", callback_data="ignore")]
                        ]
                        
                        await update.message.reply_text(
                            response,
                            parse_mode='Markdown',
                            reply_markup=InlineKeyboardMarkup(buttons)
                        )
                        
                        logger.info(f"User {user_id} confirmed and added {len(items)} products to cart via AI agent")
                        chat_histories[user_id] += f"\nCustomer: {msg_text}\nAgent: Added {len(items)} products to cart."
                        return
            
            # Check if the message contains "and" or commas, indicating multiple items
            has_multiple_indicators = ' and ' in msg_text.lower() or ',' in msg_text
            contains_numbers = bool(re.search(r'\d+', msg_text))
            
            # Try to extract multiple products if indicators are present
            if has_multiple_indicators or contains_numbers:
                logger.info(f"DEBUG - Attempting to extract multiple products")
                items = extract_multiple_products_with_quantities(msg_text, all_products, chat_history)
                
                if items:
                    # Add all items to cart
                    if user_id not in CARTS:
                        CARTS[user_id] = []
                        logger.info(f"Created new cart for user {user_id}")
                    
                    added_items = []
                    for product, quantity in items:
                        # Check if product already in cart
                        existing_item = next((item for item in CARTS[user_id] if item['product_id'] == product['id']), None)
                        
                        if existing_item:
                            existing_item['qty'] += quantity
                            added_items.append(f"{quantity} more {product['name']} (now {existing_item['qty']} total)")
                            logger.info(f"AI Agent: User {user_id} increased {product['name']} qty to {existing_item['qty']}")
                        else:
                            CARTS[user_id].append({
                                "product_id": product['id'],
                                "name": product['name'],
                                "qty": quantity,
                                "unit_price_cents": product['price_cents']
                            })
                            added_items.append(f"{quantity}x {product['name']}")
                            logger.info(f"AI Agent: User {user_id} added {quantity}x {product['name']} to cart")
                    
                    # DEBUG: Log the entire cart state
                    logger.info(f"DEBUG - Current CARTS state for user {user_id}: {CARTS[user_id]}")
                    
                    cart_total = sum(item['qty'] for item in CARTS[user_id])
                    cart_value = sum(item['qty'] * item['unit_price_cents'] for item in CARTS[user_id])
                    
                    items_list = "\n".join([f"✅ {item}" for item in added_items])
                    response = (
                        f"**Added to cart:**\n{items_list}\n\n"
                        f"🛒 Cart: {cart_total} item(s)\n"
                        f"💰 Total: {format_price(cart_value)}\n\n"
                        f"Anything else you'd like to add?"
                    )
                    
                    buttons = [
                        [InlineKeyboardButton("🛒 View Cart", callback_data="view_cart")],
                        [InlineKeyboardButton("✅ Checkout", callback_data="checkout_summary")],
                        [InlineKeyboardButton("💬 Keep Shopping", callback_data="ignore")]
                    ]
                    
                    await update.message.reply_text(
                        response,
                        parse_mode='Markdown',
                        reply_markup=InlineKeyboardMarkup(buttons)
                    )
                    
                    logger.info(f"User {user_id} added {len(items)} products to cart via AI agent")
                    chat_histories[user_id] += f"\nCustomer: {msg_text}\nAgent: Added {len(items)} products to cart."
                    return
            
            # Fall back to single product extraction if multiple extraction didn't work
            product = extract_product_from_message(msg_text, all_products, chat_history)
            logger.info(f"DEBUG - Extracted product: {product['name'] if product else 'None'}")
            
            # Extract quantity from message (e.g., "add 2 pairs", "add 5")
            quantity = 1
            qty_match = re.search(r'\b(\d+)\s*(pair|pairs|piece|pieces|unit|units)?\b', msg_text.lower())
            if qty_match:
                quantity = int(qty_match.group(1))
                logger.info(f"DEBUG - Extracted quantity: {quantity}")
                # Store quantity in user context for later reference
                if 'last_quantity' not in context.user_data:
                    context.user_data['last_quantity'] = {}
                context.user_data['last_quantity'][user_id] = quantity
            
            # Only use fallback if no product was found AND user used pronouns
            if not product:
                user_lower = msg_text.lower()
                pronoun_patterns = [r'\bit\b', r'\bthat\b', r'\bthis\b', r'\bthe one\b', r'\bthis one\b', r'\bthat one\b']
                has_pronoun = any(re.search(pattern, user_lower) for pattern in pronoun_patterns)
                
                if has_pronoun and chat_history:
                    logger.info(f"DEBUG - User used pronoun, trying to get last mentioned product")
                    product = get_last_mentioned_product(chat_history, all_products)
                    logger.info(f"DEBUG - Last mentioned product: {product['name'] if product else 'None'}")
                    
                    # If using pronoun and quantity wasn't specified, check if we stored one earlier
                    if quantity == 1 and 'last_quantity' in context.user_data:
                        stored_qty = context.user_data.get('last_quantity', {}).get(user_id, 1)
                        if stored_qty > 1:
                            quantity = stored_qty
                            logger.info(f"DEBUG - Retrieved stored quantity: {quantity}")
            
            if product:
                # Add to cart
                if user_id not in CARTS:
                    CARTS[user_id] = []
                    logger.info(f"Created new cart for user {user_id}")
                
                # Check if product already in cart
                existing_item = next((item for item in CARTS[user_id] if item['product_id'] == product['id']), None)
                
                if existing_item:
                    existing_item['qty'] += quantity
                    qty_msg = f"Added {quantity} more! You now have {existing_item['qty']} in your cart."
                    logger.info(f"AI Agent: User {user_id} increased {product['name']} qty to {existing_item['qty']}")
                else:
                    CARTS[user_id].append({
                        "product_id": product['id'],
                        "name": product['name'],
                        "qty": quantity,
                        "unit_price_cents": product['price_cents']
                    })
                    qty_msg = f"Added {quantity} to your cart!"
                    logger.info(f"AI Agent: User {user_id} added {quantity}x {product['name']} to cart. Cart now has {len(CARTS[user_id])} unique items")
                
                # DEBUG: Log the entire cart state
                logger.info(f"DEBUG - Current CARTS state for user {user_id}: {CARTS[user_id]}")
                
                # Store product context for follow-up questions
                if 'last_mentioned_product' not in context.user_data:
                    context.user_data['last_mentioned_product'] = {}
                context.user_data['last_mentioned_product'][user_id] = product['name']
                
                cart_total = sum(item['qty'] for item in CARTS[user_id])  # Total quantity, not unique items
                response = (
                    f"✅ **{product['name']}** {qty_msg}\n\n"
                    f"💰 Price: {format_price(product['price_cents'])} each\n"
                    f"🛒 Cart: {cart_total} item(s)\n\n"
                    f"Want to add more, or are you ready to checkout?"
                )
                
                buttons = [
                    [InlineKeyboardButton("🛒 View Cart", callback_data="view_cart")],
                    [InlineKeyboardButton("💬 Continue Shopping", callback_data="ignore")]
                ]
                
                await update.message.reply_text(
                    response,
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                
                logger.info(f"User {user_id} added {product['name']} to cart via AI agent")
                chat_histories[user_id] += f"\nCustomer: {msg_text}\nAgent: Added {quantity}x {product['name']} to cart."
                return
            else:
                # Couldn't identify product - provide helpful suggestions
                logger.info(f"DEBUG - Could not identify product from message: '{msg_text}'")
                logger.info(f"DEBUG - Available products count: {len(all_products)}")
                
                # Try to find similar products to suggest
                # Remove numbers from search to better match "2 pairs of shorts" to "shorts"
                search_words = set(re.sub(r'\d+', '', msg_text.lower()).split())
                search_words = {w for w in search_words if len(w) > 2}  # Filter out short words
                
                suggestions = []
                for product in all_products[:30]:  # Check first 30 products
                    product_words = set(product['name'].lower().split())
                    if search_words & product_words:
                        suggestions.append(product['name'])
                
                if suggestions:
                    suggestion_text = "\n".join([f"• {name}" for name in suggestions[:3]])
                    response = f"I couldn't find that exact item. Did you mean one of these?\n\n{suggestion_text}\n\nJust say 'add [product name]' and I'll add it for you! 😊"
                else:
                    response = "I couldn't find that item in our inventory. Could you describe it differently or browse our categories? 🔍"
                
                buttons = [
                    [InlineKeyboardButton("📁 Browse Categories", callback_data="categories")],
                    [InlineKeyboardButton("🔍 Search Products", callback_data="search_products")]
                ]
                
                await update.message.reply_text(
                    response,
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                chat_histories[user_id] += f"\nCustomer: {msg_text}\nAgent: {response}"
                return
        
        elif action_intent['action'] == 'display_product':
            # User wants to see a specific product
            chat_history = chat_histories.get(user_id, "")
            product = extract_product_from_message(msg_text, all_products, chat_history)
            
            if not product:
                product = get_last_mentioned_product(chat_histories[user_id], all_products)
            
            if product:
                # Display the product with image and details
                stock_status = "✅ In Stock" if product.get('stock', 0) > 0 else "❌ Out of Stock"
                caption = (
                    f"**{product['name']}**\n\n"
                    f"💰 Price: {format_price(product['price_cents'])}\n"
                    f"📦 {stock_status}\n"
                    f"🏷️ Category: {product.get('category', 'N/A')}\n\n"
                    f"Want to add this to your cart?"
                )
                
                img_url = product.get('image_url')
                if not img_url or not img_url.startswith('http'):
                    img_url = PLACEHOLDER_IMG
                
                buttons = [
                    [InlineKeyboardButton("➕ Add 1", callback_data=f"add_{product['id']}_1"),
                     InlineKeyboardButton("➕ Add 5", callback_data=f"add_{product['id']}_5")],
                    [InlineKeyboardButton("📋 Full Details", callback_data=f"prod_{product['id']}")],
                    [InlineKeyboardButton("💬 Keep Chatting", callback_data="ignore")]
                ]
                
                try:
                    await update.message.reply_photo(
                        photo=img_url,
                        caption=caption,
                        reply_markup=InlineKeyboardMarkup(buttons),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Error sending product image: {e}")
                    await update.message.reply_text(
                        caption,
                        reply_markup=InlineKeyboardMarkup(buttons),
                        parse_mode="Markdown"
                    )
                
                logger.info(f"User {user_id} requested display of {product['name']} via AI agent")
                chat_histories[user_id] += f"\nCustomer: {msg_text}\nAgent: Displayed {product['name']}."
                return
            else:
                # Couldn't identify product
                response = "Which product would you like to see? Let me know the name and I'll show it to you! 👀"
                await update.message.reply_text(response)
                chat_histories[user_id] += f"\nCustomer: {msg_text}\nAgent: {response}"
                return
        
        # If action was detected and handled above, don't call AI conversation
        if action_intent['action'] in ['add_to_cart', 'display_product']:
            logger.info(f"DEBUG - Action '{action_intent['action']}' was handled, skipping AI conversation")
            return
        
        # === Farewell Handling (after action processing) ===
        farewell_keywords = ["bye", "goodbye", "see you", "good night",
                             "im done", "i'm done", "stop", "exit", "quit"]
        # Only check farewell if message DOESN'T contain action keywords
        has_action = any(word in msg_text.lower() for word in ['add', 'show', 'buy', 'get', 'purchase', 'want'])
        
        if not has_action and any(word in normalize_name(msg_text) for word in farewell_keywords):
            farewell_responses = [
                "👋 Alright! It was great chatting with you. See you next time!",
                "😊 Thanks for stopping by! Come back anytime.",
                "🛍️ Take care! Hope to see you again soon.",
                "👍 Got it — I'll be here when you need me next!"
            ]
            goodbye_message = random.choice(farewell_responses)
            chat_histories[user_id] = ""
            user_chat_contexts[user_id] = None
            user_in_chat_mode[user_id] = False
            
            # Show main menu button when exiting chat mode
            await update.message.reply_text(
                goodbye_message,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="start")]])
            )
            logger.info(f"User {user_id} exited chat mode with farewell")
            return
        
        # Normal conversation flow with GPT-4 function calling
        chat_histories[user_id] += f"\nCustomer: {msg_text}"
        
        # Get AI response with product recommendations
        reply, product_ids = get_sales_recommendations(msg_text, chat_histories[user_id], all_products, user_id)
        
        # Add AI response to history
        chat_histories[user_id] += f"\nAgent: {reply}"
        
        # Display the sales pitch
        await update.message.reply_text(reply, parse_mode='Markdown')
        
        # If AI recommended products, show them as buttons
        if product_ids:
            # Build product list text
            recommended_products = [p for p in all_products if p['id'] in product_ids]
            
            if recommended_products:
                text = (
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "✨ **MY TOP PICKS FOR YOU** ✨\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                )
                for idx, p in enumerate(recommended_products[:5], 1):  # Limit to 5 products
                    text += (
                        f"{idx}️⃣  **{p['name']}**\n"
                        f"   💰 {format_price(p['price_cents'])}\n"
                        f"   📦 In Stock: {p['stock']} available\n\n"
                    )
                
                # Create buttons for each product (organize in 2 columns)
                buttons = []
                products_to_show = recommended_products[:5]
                for i in range(0, len(products_to_show), 2):
                    row = []
                    for j in range(i, min(i + 2, len(products_to_show))):
                        p = products_to_show[j]
                        row.append(InlineKeyboardButton(f"👀 {p['name'][:15]}...", callback_data=f"prod_{p['id']}"))
                    buttons.append(row)
                
                await update.message.reply_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode='Markdown'
                )

        
        logger.info(f"User {user_id} chatting with AI agent (GPT-4 function calling)")
        
        # Extract and store conversation context after each exchange
        extracted_context = extract_relevant_products(chat_histories[user_id], all_products)
        if extracted_context:
            user_chat_contexts[user_id] = extracted_context
            logger.info(f"Updated user {user_id} context: {extracted_context}")
        return

    # 5. FALLBACK - Unrecognized command
    await update.message.reply_text(
        "I'm not sure what you mean. Use /start for main menu or /cancel to exit current operation."
    )

async def payment_method_paypal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle PayPal payment selection"""
    query = update.callback_query
    await query.answer()
    
    user_id = get_or_create_user(update.effective_user)
    cart = context.user_data.get('cart_snapshot')
    total_cents = context.user_data.get('total_snapshot')
    address = context.user_data.get('delivery_address')
    
    if not cart or not address:
        await query.message.reply_text("Session expired. Please start again.")
        return
    
    # Check if discount is applied
    discount_info = context.user_data.get('applied_discount')
    discount_code = context.user_data.get('discount_code')
    
    # Use discounted total if discount applied, otherwise use original total
    payment_total_cents = discount_info['final_cents'] if discount_info else total_cents
    
    # Store pending order data in global dict using user_id as key
    PENDING_ORDERS[user_id] = {
        'cart': cart,
        'total_cents': payment_total_cents,  # Store the final amount to charge
        'address': address,
        'user_id': user_id,
        'payment_method': 'PayPal',
        'discount_id': discount_code['id'] if discount_code else None,
        'discount_amount_cents': discount_info['discount_cents'] if discount_info else None
    }
    
    # Create PayPal payment
    access_token = get_paypal_access_token()
    if not access_token:
        await query.message.reply_text("Payment System Error.")
        return

    amount = payment_total_cents / 100.0
    paypal_payload = {
        "intent": "CAPTURE",
        "purchase_units": [{"reference_id": str(user_id), "amount": {"currency_code": "SGD", "value": f"{amount:.2f}"}}],
        "application_context": {
            "return_url": f"{REDIRECT_URL}?user_id={user_id}",
            "cancel_url": f"{REDIRECT_URL}?user_id={user_id}&status=cancelled"
        }
    }
    
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
    resp = requests.post(f"{PAYPAL_API_BASE_URL}/v2/checkout/orders", json=paypal_payload, headers=headers)
    
    if resp.status_code != 201:
        await query.message.reply_text("Payment creation failed. Please try again.")
        return

    paypal_order = resp.json()
    approval_url = next((link['href'] for link in paypal_order.get('links', []) if link['rel'] == 'approve'), None)
    
    if approval_url:
        # Build message with discount info if applicable
        message_text = ""
        if discount_info:
            message_text += f"💰 Original: {format_price(discount_info['original_cents'])}\n"
            message_text += f"🎟️ Discount: -{format_price(discount_info['discount_cents'])}\n"
            message_text += f"   ({discount_info['discount_description']})\n\n"
        
        message_text += f"**💵 Total Amount: {format_price(payment_total_cents)}**\n\n"
        message_text += f"💳 Click the button below to complete payment via PayPal:\n\n"
        message_text += f"✨ Your order will be created after successful payment."
        
        await query.message.reply_text(
            message_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Pay with PayPal", url=approval_url)],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="start")]
            ]),
            parse_mode='Markdown'
        )
        # Clear cart but keep pending order
        context.user_data.clear()
        CARTS[user_id] = []
        logger.info(f"Created PayPal payment for user {user_id}, Amount: {format_price(payment_total_cents)}")
    else:
        await query.message.reply_text("Could not generate payment link.")

async def payment_confirmed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payment confirmation button"""
    query = update.callback_query
    await query.answer()
    
    order_id = query.data.split('_')[1]
    
    await query.message.reply_text(
        f"Thank you for confirming payment for Order #{order_id}! 🎉\n\n"
        f"We're processing your order. You'll receive a confirmation once verified.\n\n"
        f"If you have your transaction reference, please send:\n"
        f"`/confirm {order_id} <transaction_ref>`",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="start")]])
    )

async def resume_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payment resume for awaiting_payment orders - show payment method selection"""
    query = update.callback_query
    await query.answer()
    
    order_id = int(query.data.split('_')[2])
    user_id = get_or_create_user(update.effective_user)
    
    # Verify order belongs to user and is awaiting payment
    try:
        with DatabaseConnection() as (cur, conn):
            cur.execute("""
                SELECT * 
                FROM orders 
                WHERE id = %s AND user_id = %s AND status = 'awaiting_payment'
            """, (order_id, user_id))
            order = cur.fetchone()
    except Exception as e:
        logger.error(f"Error fetching order: {e}")
        await query.message.reply_text("Error retrieving order.")
        return
    
    if not order:
        await query.message.reply_text("Order not found or already paid/cancelled.")
        return
    
    # Store order info for payment method handlers
    context.user_data['resume_order_id'] = order_id
    context.user_data['resume_order_total'] = order['total_cents']
    context.user_data['resume_order_address'] = order.get('address', 'N/A')
    
    # Show payment method selection (matching checkout format)
    try:
        await query.edit_message_text(
            f"✅ Ready to complete payment!\n\n"
            f"💰 **Total: {format_price(order['total_cents'])}**\n\n"
            f"Please select your payment method:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 PayPal", callback_data=f"resume_paypal_{order_id}")],
                [InlineKeyboardButton("📱 PayNow QR", callback_data=f"resume_paynow_{order_id}")],
                [InlineKeyboardButton("🔙 Cancel", callback_data="my_orders")]
            ]),
            parse_mode='Markdown'
        )
    except:
        await query.message.reply_text(
            f"✅ Ready to complete payment!\n\n"
            f"💰 **Total: {format_price(order['total_cents'])}**\n\n"
            f"Please select your payment method:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 PayPal", callback_data=f"resume_paypal_{order_id}")],
                [InlineKeyboardButton("📱 PayNow QR", callback_data=f"resume_paynow_{order_id}")],
                [InlineKeyboardButton("🔙 Cancel", callback_data="my_orders")]
            ]),
            parse_mode='Markdown'
        )
    
    logger.info(f"User {user_id} selecting payment method for Order #{order_id}")


async def resume_paynow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle HitPay payment for resumed orders"""
    query = update.callback_query
    await query.answer()
    
    order_id = int(query.data.split('_')[2])
    user_id = get_or_create_user(update.effective_user)
    
    # Get order info
    order_total = context.user_data.get('resume_order_total')
    if not order_total:
        await query.message.reply_text("Session expired. Please try again.")
        return
    
    # Create HitPay payment
    amount = order_total / 100.0
    telegram_user = update.effective_user
    customer_name = telegram_user.full_name or "Customer"
    customer_email = f"user{user_id}@telegram.local"
    
    payment_result = create_hitpay_payment(
        amount=amount,
        currency='SGD',
        order_id=order_id,
        customer_name=customer_name,
        customer_email=customer_email
    )
    
    if not payment_result:
        await query.message.reply_text(
            "❌ Payment system error. Please try again later.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="start")]])
        )
        return
    
    payment_url = payment_result.get('url')
    payment_id = payment_result.get('id')
    
    # Generate QR code
    qr_image = generate_qr_code(payment_url)
    
    # Send payment info
    await query.message.reply_photo(
        photo=qr_image,
        caption=(
            f"💳 HitPay Payment - Order #{order_id}\n\n"
            f"💰 Amount: {format_price(order_total)}\n\n"
            f"📱 Scan QR code to pay with:\n"
            f"   • PayNow\n"
            f"   • Credit/Debit Card\n"
            f"   • GrabPay, Alipay+, etc.\n\n"
            f"Or click the button below.\n\n"
            f"✨ You'll receive confirmation automatically!"
        ),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Open Payment Page", url=payment_url)],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="start")]
        ])
    )
    
    logger.info(f"User {user_id} resumed payment (HitPay) for Order #{order_id}, Payment ID: {payment_id}")


async def resume_paypal_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle PayPal payment for resumed orders"""
    query = update.callback_query
    await query.answer()
    
    order_id = int(query.data.split('_')[2])
    user_id = get_or_create_user(update.effective_user)
    
    # Get order info
    order_total = context.user_data.get('resume_order_total')
    if not order_total:
        await query.message.reply_text("Session expired. Please try again.")
        return
    
    # Create PayPal payment
    access_token = get_paypal_access_token()
    if not access_token:
        await query.message.reply_text("Payment System Error.")
        return

    amount = order_total / 100.0
    paypal_payload = {
        "intent": "CAPTURE",
        "purchase_units": [{"reference_id": str(order_id), "amount": {"currency_code": "SGD", "value": f"{amount:.2f}"}}],
        "application_context": {
            "return_url": f"{REDIRECT_URL}?order_id={order_id}",
            "cancel_url": f"{REDIRECT_URL}?order_id={order_id}&status=cancelled"
        }
    }
    
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
    resp = requests.post(f"{PAYPAL_API_BASE_URL}/v2/checkout/orders", json=paypal_payload, headers=headers)
    
    if resp.status_code != 201:
        await query.message.reply_text(
            "❌ Payment creation failed. Please try again later.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="start")]])
        )
        return

    paypal_order = resp.json()
    approval_url = next((link['href'] for link in paypal_order.get('links', []) if link['rel'] == 'approve'), None)
    
    if not approval_url:
        await query.message.reply_text("Error creating payment link.")
        return
    
    # Send PayPal link (no QR for PayPal)
    try:
        await query.edit_message_text(
            f"✅ **Order #{order_id} Payment Link Ready!**\n\n"
            f"💰 Amount: **{format_price(order_total)}**\n\n"
            f"Click the button below to complete your payment with PayPal.\n\n"
            f"✨ You'll receive confirmation automatically!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Pay with PayPal", url=approval_url)],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="start")]
            ]),
            parse_mode='Markdown'
        )
    except:
        await query.message.reply_text(
            f"✅ **Order #{order_id} Payment Link Ready!**\n\n"
            f"💰 Amount: **{format_price(order_total)}**\n\n"
            f"Click the button below to complete your payment with PayPal.\n\n"
            f"✨ You'll receive confirmation automatically!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Pay with PayPal", url=approval_url)],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="start")]
            ]),
            parse_mode='Markdown'
        )
    
    logger.info(f"User {user_id} resumed payment (PayPal) for Order #{order_id}, PayPal Order: {paypal_order['id']}")

async def payment_method_paynow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle HitPay payment selection (supports PayNow QR + cards + e-wallets)"""
    query = update.callback_query
    await query.answer()
    
    user_id = get_or_create_user(update.effective_user)
    cart = context.user_data.get('cart_snapshot')
    total_cents = context.user_data.get('total_snapshot')
    address = context.user_data.get('delivery_address')
    
    if not cart or not address:
        await query.message.reply_text("Session expired. Please start again.")
        return
    
    # Check if discount is applied
    discount_info = context.user_data.get('applied_discount')
    discount_code = context.user_data.get('discount_code')
    
    # Use discounted total if discount applied, otherwise use original total
    payment_total_cents = discount_info['final_cents'] if discount_info else total_cents
    
    # Store pending order data
    PENDING_ORDERS[user_id] = {
        'cart': cart,
        'total_cents': payment_total_cents,  # Store the final amount to charge
        'address': address,
        'user_id': user_id,
        'payment_method': 'HitPay',
        'discount_id': discount_code['id'] if discount_code else None,
        'discount_amount_cents': discount_info['discount_cents'] if discount_info else None
    }
    
    # Create HitPay payment
    amount = payment_total_cents / 100.0
    telegram_user = update.effective_user
    customer_name = telegram_user.full_name or "Customer"
    customer_email = f"user{user_id}@telegram.local"
    
    payment_result = create_hitpay_payment(
        amount=amount,
        currency='SGD',
        order_id=user_id,  # Use user_id as reference
        customer_name=customer_name,
        customer_email=customer_email
    )
    
    if not payment_result:
        await query.message.reply_text(
            "❌ Payment system error. Please try again or contact support.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="start")]])
        )
        return
    
    payment_url = payment_result.get('url')
    payment_id = payment_result.get('id')
    
    # Generate QR code for payment URL
    qr_image = generate_qr_code(payment_url)
    
    # Build caption with discount info if applicable
    caption_text = ""
    if discount_info:
        caption_text += f"💰 Original: {format_price(discount_info['original_cents'])}\n"
        caption_text += f"🎟️ Discount: -{format_price(discount_info['discount_cents'])}\n"
        caption_text += f"   ({discount_info['discount_description']})\n\n"
    
    caption_text += f"**💵 Amount: {format_price(payment_total_cents)}**\n\n"
    caption_text += f"📱 **Scan QR code** to pay with:\n"
    caption_text += f"   • PayNow\n"
    caption_text += f"   • Credit/Debit Card\n"
    caption_text += f"   • GrabPay, Alipay+, etc.\n\n"
    caption_text += f"Or click the button below to open payment page.\n\n"
    caption_text += f"✨ Your order will be created after successful payment!"
    
    # Send QR code with instructions
    await query.message.reply_photo(
        photo=qr_image,
        caption=caption_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Open Payment Page", url=payment_url)],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="start")]
        ])
    )
    
    # Clear cart but keep pending order
    context.user_data.clear()
    CARTS[user_id] = []
    
    logger.info(f"Created HitPay payment for user {user_id}, Amount: {format_price(payment_total_cents)}, Payment ID: {payment_id}")

# --- Main ---
def start_bot():
    global BOT_APP, BOT_LOOP
    try:
        BOT_LOOP = asyncio.get_event_loop()
    except RuntimeError:
        BOT_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(BOT_LOOP)

    req = HTTPXRequest(connection_pool_size=8, read_timeout=30.0, write_timeout=30.0, connect_timeout=30.0)

    BOT_APP = ApplicationBuilder().token(TELEGRAM_TOKEN).request(req).build()
    
    # Command Handlers
    BOT_APP.add_handler(CommandHandler("start", start))
    BOT_APP.add_handler(CommandHandler("cancel", cancel_handler))
    BOT_APP.add_handler(CommandHandler("search", search_products_handler))
    
    # Message Handlers (photos and text)
    BOT_APP.add_handler(MessageHandler(filters.PHOTO, general_text_handler))
    BOT_APP.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, general_text_handler))

    # Main Menu Callbacks
    BOT_APP.add_handler(CallbackQueryHandler(start, pattern="^start$"))
    BOT_APP.add_handler(CallbackQueryHandler(help_handler, pattern="^help$"))
    BOT_APP.add_handler(CallbackQueryHandler(chat_with_agent_handler, pattern="^chat_with_agent$"))
    BOT_APP.add_handler(CallbackQueryHandler(continue_chat_handler, pattern="^continue_chat$"))
    BOT_APP.add_handler(CallbackQueryHandler(search_products_handler, pattern="^search_products$"))
    
    # Category & Product Callbacks
    BOT_APP.add_handler(CallbackQueryHandler(categories_handler, pattern="^categories$"))
    BOT_APP.add_handler(CallbackQueryHandler(products_handler, pattern="^cat_"))
    BOT_APP.add_handler(CallbackQueryHandler(product_detail_handler, pattern="^prod_"))
    
    # Virtual Try-On Callback
    BOT_APP.add_handler(CallbackQueryHandler(lambda u, c: execute_tryon(u, int(u.callback_query.data.split('_')[1])), pattern="^tryon_"))
    
    # Cart Callbacks
    BOT_APP.add_handler(CallbackQueryHandler(select_quantity_handler, pattern="^selqty_"))
    BOT_APP.add_handler(CallbackQueryHandler(prompt_custom_quantity_handler, pattern="^askqty_"))
    BOT_APP.add_handler(CallbackQueryHandler(add_to_cart_handler, pattern="^add_"))
    BOT_APP.add_handler(CallbackQueryHandler(view_cart_handler, pattern="^view_cart$"))
    BOT_APP.add_handler(CallbackQueryHandler(modify_cart_handler, pattern="^(inc|dec|remove)_\d+$"))
    BOT_APP.add_handler(CallbackQueryHandler(clear_cart_handler, pattern="^clear_cart$"))
    
    # Checkout & Orders
    BOT_APP.add_handler(CallbackQueryHandler(checkout_summary_handler, pattern="^checkout_summary$"))
    BOT_APP.add_handler(CallbackQueryHandler(use_saved_address_handler, pattern="^use_saved_address$"))
    BOT_APP.add_handler(CallbackQueryHandler(enter_new_address_handler, pattern="^enter_new_address$"))
    BOT_APP.add_handler(CallbackQueryHandler(apply_discount_code_handler, pattern="^apply_discount_code$"))
    BOT_APP.add_handler(CallbackQueryHandler(remove_discount_handler, pattern="^remove_discount$"))
    BOT_APP.add_handler(CallbackQueryHandler(payment_method_paypal_handler, pattern="^pay_paypal$"))
    BOT_APP.add_handler(CallbackQueryHandler(payment_method_paynow_handler, pattern="^pay_paynow$"))
    BOT_APP.add_handler(CallbackQueryHandler(payment_confirmed_handler, pattern="^paid_"))
    BOT_APP.add_handler(CallbackQueryHandler(resume_payment_handler, pattern="^pay_order_"))
    BOT_APP.add_handler(CallbackQueryHandler(resume_paynow_handler, pattern="^resume_paynow_"))
    BOT_APP.add_handler(CallbackQueryHandler(resume_paypal_handler, pattern="^resume_paypal_"))
    BOT_APP.add_handler(CallbackQueryHandler(my_orders_handler, pattern="^my_orders$"))
    BOT_APP.add_handler(CallbackQueryHandler(last_order_handler, pattern="^last_order$"))
    
    # Utility
    BOT_APP.add_handler(CallbackQueryHandler(lambda u,c: u.callback_query.answer(), pattern="^ignore$")) 

    logger.info("Bot started and polling for updates...")
    print("Bot is polling...")
    BOT_APP.run_polling()

if __name__ == "__main__":
    import threading
    threading.Thread(target=start_bot).start()
    app.run(port=5000)
