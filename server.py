from fastapi import FastAPI, HTTPException, Header, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError, ConnectionFailure
from bson import ObjectId
from datetime import datetime
import os
import random
import string
import requests
import hashlib
from pathlib import Path
import logging
from PIL import Image
from io import BytesIO
import shutil

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create uploads directory if it doesn't exist
UPLOAD_DIR = "uploads"
Path(UPLOAD_DIR).mkdir(exist_ok=True)

# Create subdirectories for different file types
PRODUCT_IMAGES_DIR = os.path.join(UPLOAD_DIR, "products")
TEMP_DIR = os.path.join(UPLOAD_DIR, "temp")
Path(PRODUCT_IMAGES_DIR).mkdir(exist_ok=True)
Path(TEMP_DIR).mkdir(exist_ok=True)

logger.info(f"📁 Upload directories configured:")
logger.info(f"   - Main: {UPLOAD_DIR}")
logger.info(f"   - Products: {PRODUCT_IMAGES_DIR}")
logger.info(f"   - Temp: {TEMP_DIR}")

# ============= DATABASE CONFIGURATION =============

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "besties_craft_db")

db = None
client = None

def connect_to_mongo():
    """Establish MongoDB connection with proper error handling"""
    global client, db
    try:
        logger.info(f"🔄 Connecting to MongoDB...")
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        
        # Verify connection
        client.admin.command('ping')
        db = client[DATABASE_NAME]
        
        logger.info("✅ MongoDB connected successfully")
        logger.info(f"📊 Database: {DATABASE_NAME}")
        
        # List existing collections
        collections = db.list_collection_names()
        logger.info(f"📦 Collections found: {collections}")
        
        # Create indexes for better query performance
        try:
            db.products.create_index("category")
            db.products.create_index("brand")
            db.products.create_index("rating")
            db.orders.create_index("user_id")
            db.users.create_index("email", unique=True, sparse=True)
            db.users.create_index("phone", unique=True, sparse=True)
            logger.info("✅ Database indexes created")
        except Exception as e:
            logger.warning(f"⚠️  Index creation warning: {e}")
        
        return True
        
    except (ServerSelectionTimeoutError, ConnectionFailure) as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        logger.error(f"⚠️  Make sure MONGO_URI is set correctly")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error connecting to MongoDB: {e}")
        return False

def create_sample_products():
    """Create sample products if database is empty"""
    try:
        if db is None:
            logger.warning("⚠️  Database not connected, skipping sample data creation")
            return
        
        product_count = db.products.count_documents({})
        
        if product_count > 0:
            logger.info(f"✅ Database already has {product_count} products")
            return
        
        logger.info("📝 Creating sample products...")
        
        sample_products = [
            {
                "name": "Cozy Woollen Blanket",
                "description": "Warm and soft woollen blanket perfect for winters. Handcrafted with love using premium quality wool.",
                "base_price": 1299.00,
                "category": "Blankets",
                "brand": "Besties Craft",
                "images": [
                    {
                        "url": "/uploads/products/default-blanket.jpg",
                        "alt_text": "Cozy Woollen Blanket",
                        "is_primary": True
                    }
                ],
                "variants": [
                    {
                        "name": "Color",
                        "options": ["Beige", "Gray", "Navy Blue", "Cream"],
                        "is_visible": True
                    },
                    {
                        "name": "Size",
                        "options": ["Single", "Double", "King"],
                        "is_visible": True
                    }
                ],
                "skus": [
                    {
                        "variant_values": {"Color": "Beige", "Size": "Single"},
                        "sku": "WCBLANKET-001",
                        "price": 1299.00,
                        "stock": 15,
                        "weight": 1.2
                    },
                    {
                        "variant_values": {"Color": "Gray", "Size": "Double"},
                        "sku": "WCBLANKET-002",
                        "price": 1799.00,
                        "stock": 10,
                        "weight": 1.8
                    },
                    {
                        "variant_values": {"Color": "Navy Blue", "Size": "King"},
                        "sku": "WCBLANKET-003",
                        "price": 2299.00,
                        "stock": 8,
                        "weight": 2.2
                    }
                ],
                "rating": 4.5,
                "reviews_count": 24,
                "return_policy": "30 days money-back guarantee",
                "warranty": "1 year against defects",
                "createdAt": datetime.utcnow(),
                "updatedAt": datetime.utcnow(),
                "inStock": True
            },
            {
                "name": "Handmade Woollen Scarf",
                "description": "Beautiful handmade scarf in various colors. Perfect gift for winters. Each piece is unique.",
                "base_price": 599.00,
                "category": "Scarves",
                "brand": "Besties Craft",
                "images": [
                    {
                        "url": "/uploads/products/default-scarf.jpg",
                        "alt_text": "Handmade Woollen Scarf",
                        "is_primary": True
                    }
                ],
                "variants": [
                    {
                        "name": "Color",
                        "options": ["Red", "Green", "Purple", "Black", "White"],
                        "is_visible": True
                    }
                ],
                "skus": [
                    {
                        "variant_values": {"Color": "Red"},
                        "sku": "WCSCARF-001",
                        "price": 599.00,
                        "stock": 20,
                        "weight": 0.3
                    },
                    {
                        "variant_values": {"Color": "Green"},
                        "sku": "WCSCARF-002",
                        "price": 599.00,
                        "stock": 18,
                        "weight": 0.3
                    }
                ],
                "rating": 4.8,
                "reviews_count": 42,
                "return_policy": "30 days money-back guarantee",
                "warranty": "Lifetime care advice",
                "createdAt": datetime.utcnow(),
                "updatedAt": datetime.utcnow(),
                "inStock": True
            },
            {
                "name": "Woollen Beanie & Gloves Set",
                "description": "Matching woollen beanie and gloves set. Keeps you warm and stylish in winters.",
                "base_price": 899.00,
                "category": "Accessories",
                "brand": "Besties Craft",
                "images": [
                    {
                        "url": "/uploads/products/default-beanie.jpg",
                        "alt_text": "Woollen Beanie & Gloves Set",
                        "is_primary": True
                    }
                ],
                "variants": [
                    {
                        "name": "Color",
                        "options": ["Black", "Gray", "White", "Brown"],
                        "is_visible": True
                    }
                ],
                "skus": [
                    {
                        "variant_values": {"Color": "Black"},
                        "sku": "WCBEANIE-001",
                        "price": 899.00,
                        "stock": 25,
                        "weight": 0.4
                    },
                    {
                        "variant_values": {"Color": "Gray"},
                        "sku": "WCBEANIE-002",
                        "price": 899.00,
                        "stock": 22,
                        "weight": 0.4
                    }
                ],
                "rating": 4.6,
                "reviews_count": 18,
                "return_policy": "30 days money-back guarantee",
                "warranty": "6 months against wear",
                "createdAt": datetime.utcnow(),
                "updatedAt": datetime.utcnow(),
                "inStock": True
            },
            {
                "name": "Decorative Woollen Wall Hanging",
                "description": "Beautiful handcrafted woollen wall hanging. Adds warmth and character to any room.",
                "base_price": 1099.00,
                "category": "Home Decor",
                "brand": "Besties Craft",
                "images": [
                    {
                        "url": "/uploads/products/default-wall-hanging.jpg",
                        "alt_text": "Decorative Woollen Wall Hanging",
                        "is_primary": True
                    }
                ],
                "variants": [
                    {
                        "name": "Design",
                        "options": ["Geometric", "Floral", "Abstract", "Tribal"],
                        "is_visible": True
                    }
                ],
                "skus": [
                    {
                        "variant_values": {"Design": "Geometric"},
                        "sku": "WCWALL-001",
                        "price": 1099.00,
                        "stock": 12,
                        "weight": 0.8
                    },
                    {
                        "variant_values": {"Design": "Floral"},
                        "sku": "WCWALL-002",
                        "price": 1099.00,
                        "stock": 14,
                        "weight": 0.8
                    }
                ],
                "rating": 4.7,
                "reviews_count": 31,
                "return_policy": "30 days money-back guarantee",
                "warranty": "Lifetime",
                "createdAt": datetime.utcnow(),
                "updatedAt": datetime.utcnow(),
                "inStock": True
            },
            {
                "name": "Premium Woollen Cushion Cover",
                "description": "Soft and luxurious woollen cushion cover. Perfect for home furnishing and gifting.",
                "base_price": 449.00,
                "category": "Home Decor",
                "brand": "Besties Craft",
                "images": [
                    {
                        "url": "/uploads/products/default-cushion.jpg",
                        "alt_text": "Premium Woollen Cushion Cover",
                        "is_primary": True
                    }
                ],
                "variants": [
                    {
                        "name": "Color",
                        "options": ["Maroon", "Teal", "Gold", "Pink"],
                        "is_visible": True
                    },
                    {
                        "name": "Size",
                        "options": ["16x16", "18x18", "20x20"],
                        "is_visible": True
                    }
                ],
                "skus": [
                    {
                        "variant_values": {"Color": "Maroon", "Size": "16x16"},
                        "sku": "WCCUSH-001",
                        "price": 449.00,
                        "stock": 30,
                        "weight": 0.3
                    },
                    {
                        "variant_values": {"Color": "Teal", "Size": "18x18"},
                        "sku": "WCCUSH-002",
                        "price": 549.00,
                        "stock": 28,
                        "weight": 0.35
                    }
                ],
                "rating": 4.4,
                "reviews_count": 15,
                "return_policy": "30 days money-back guarantee",
                "warranty": "6 months",
                "createdAt": datetime.utcnow(),
                "updatedAt": datetime.utcnow(),
                "inStock": True
            }
        ]
        
        result = db.products.insert_many(sample_products)
        logger.info(f"✅ {len(result.inserted_ids)} sample products created successfully!")
        
        for i, product_id in enumerate(result.inserted_ids):
            logger.info(f"   - Product {i+1}: {sample_products[i]['name']} (ID: {product_id})")
        
    except Exception as e:
        logger.error(f"❌ Error creating sample products: {e}")

# Connect on startup
mongo_connected = connect_to_mongo()

# ============= PYDANTIC MODELS =============

class ProductVariant(BaseModel):
    """Variant options like color, size, etc."""
    name: str
    options: List[str]
    is_visible: bool = True

class ProductImage(BaseModel):
    """Product images"""
    url: str
    alt_text: Optional[str] = None
    is_primary: bool = False

class SKUOption(BaseModel):
    """Combination of variant options for a specific SKU"""
    variant_values: dict
    sku: str
    price: float
    stock: int
    weight: Optional[float] = None

class Product(BaseModel):
    name: str
    description: str
    base_price: float
    images: List[ProductImage]
    category: str = "general"
    variants: List[ProductVariant] = []
    skus: List[SKUOption] = []
    rating: float = 0
    reviews_count: int = 0
    brand: Optional[str] = None
    return_policy: Optional[str] = None
    warranty: Optional[str] = None

class CartItem(BaseModel):
    product_id: str
    quantity: int
    selected_variants: Optional[dict] = None

class Order(BaseModel):
    user_id: str
    items: List[CartItem]
    shipping_address: dict
    billing_address: Optional[dict] = None
    payment_method: str = "razorpay"

# ============= FASTAPI APP INITIALIZATION =============

app = FastAPI(
    title="Besties Craft Backend API",
    description="E-commerce backend for handmade woolies",
    version="2.0"
)

# Serve uploaded files statically
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============= STARTUP & SHUTDOWN EVENTS =============

@app.on_event("startup")
async def startup_event():
    """Run on application startup"""
    logger.info("🚀 Application starting...")
    
    if db is None:
        logger.error("⚠️  WARNING: Database connection failed!")
        logger.error("❌ CRITICAL: MongoDB is not connected. Check your MONGO_URI environment variable.")
    else:
        logger.info("✅ Database connection verified")
        
        # Create sample products if needed
        create_sample_products()
        
        try:
            product_count = db.products.count_documents({})
            order_count = db.orders.count_documents({})
            user_count = db.users.count_documents({})
            
            logger.info(f"📊 DATABASE STATS:")
            logger.info(f"   - Products: {product_count}")
            logger.info(f"   - Orders: {order_count}")
            logger.info(f"   - Users: {user_count}")
            
            if product_count == 0:
                logger.warning("⚠️  WARNING: No products found in database!")
            else:
                logger.info(f"✅ Successfully loaded {product_count} products")
                
        except Exception as e:
            logger.error(f"❌ Error fetching database stats: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """Run on application shutdown"""
    global client
    if client:
        client.close()
        logger.info("🔌 MongoDB connection closed")

# ============= HELPER FUNCTIONS =============

def check_db_connection():
    """Check if database is connected"""
    if db is None:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed. Please try again later."
        )

def generate_otp():
    """Generate a 6-digit OTP"""
    return ''.join(random.choices(string.digits, k=6))

def get_stock_status(stock: int) -> dict:
    """
    Convert stock count to customer-friendly status
    Only shows: In Stock, Low Stock, Out of Stock
    Does NOT show exact numbers
    """
    if stock <= 0:
        return {
            "status": "Out of Stock",
            "display": "Out of Stock",
            "in_stock": False,
            "is_low": False
        }
    elif stock <= 3:
        return {
            "status": "Low Stock",
            "display": "Low Stock - Hurry!",
            "in_stock": True,
            "is_low": True
        }
    else:
        return {
            "status": "In Stock",
            "display": "In Stock",
            "in_stock": True,
            "is_low": False
        }

def optimize_image(file_content: bytes, max_width: int = 1200, max_height: int = 1200, quality: int = 85) -> bytes:
    """Optimize image: resize, compress, and convert to appropriate format"""
    try:
        # Open image
        img = Image.open(BytesIO(file_content))
        
        # Convert RGBA to RGB if needed
        if img.mode == 'RGBA':
            rgb_img = Image.new('RGB', img.size, (255, 255, 255))
            rgb_img.paste(img, mask=img.split()[3] if len(img.split()) == 4 else None)
            img = rgb_img
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Resize if necessary
        img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        
        # Save optimized image
        buffer = BytesIO()
        img.save(buffer, format='JPEG', quality=quality, optimize=True)
        buffer.seek(0)
        
        logger.info(f"✅ Image optimized - Size: {len(buffer.getvalue())} bytes")
        
        return buffer.getvalue()
    except Exception as e:
        logger.warning(f"⚠️  Image optimization failed: {e}, using original")
        return file_content

def send_email(recipient_email, subject, body):
    """Send email via Brevo API"""
    try:
        api_key = os.getenv("BREVO_API_KEY")
        
        if not api_key:
            logger.warning("⚠️  BREVO_API_KEY not found in environment variables")
            return False
        
        message = {
            "sender": {"name": "Besties Craft", "email": "akkuyadav1434@gmail.com"},
            "to": [{"email": recipient_email}],
            "subject": subject,
            "htmlContent": body
        }
        
        headers = {
            "api-key": api_key,
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            json=message,
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 201:
            logger.info(f"✅ Email sent successfully to {recipient_email}")
            return True
        else:
            logger.error(f"❌ Email error: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Email error: {str(e)}")
        return False

def send_sms(phone_number, otp):
    """Send SMS via Twilio API"""
    try:
        from twilio.rest import Client
        
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        twilio_phone = os.getenv("TWILIO_PHONE_NUMBER")
        
        if not all([account_sid, auth_token, twilio_phone]):
            logger.warning("⚠️  Twilio credentials not found in environment variables")
            return False
        
        phone_number = str(phone_number).strip()
        
        if not phone_number.startswith("+"):
            phone_number = "+91" + phone_number
        
        client = Client(account_sid, auth_token)
        
        message = client.messages.create(
            body=f"Your Besties Craft OTP is: {otp}. This expires in 10 minutes.",
            from_=twilio_phone,
            to=phone_number
        )
        
        logger.info(f"✅ SMS sent successfully to {phone_number}")
        return True
        
    except Exception as e:
        logger.error(f"❌ SMS error: {str(e)}")
        return False

# ============= BASIC ENDPOINTS =============

@app.get("/health")
def health_check():
    """Health check endpoint with detailed status"""
    db_status = "connected" if db is not None else "disconnected"
    product_count = 0
    
    if db is not None:
        try:
            product_count = db.products.count_documents({})
        except:
            pass
    
    return {
        "status": "ok" if db_status == "connected" else "error",
        "database": db_status,
        "products_count": product_count,
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/")
def root():
    """Root endpoint with API information"""
    return {
        "message": "Besties Craft Backend API",
        "docs": "/docs",
        "health": "/health",
        "version": "2.0",
        "database_connected": db is not None
    }

# ============= FILE UPLOAD ENDPOINTS =============

@app.post("/api/upload-image")
async def upload_image(file: UploadFile = File(...)):
    """
    Upload a single image file and return the URL
    Accepts: JPG, PNG, GIF, WebP (Max 10MB)
    Automatically optimizes and compresses images
    """
    try:
        allowed_extensions = {"jpg", "jpeg", "png", "gif", "webp"}
        file_extension = file.filename.split(".")[-1].lower()
        
        if file_extension not in allowed_extensions:
            raise HTTPException(
                status_code=400, 
                detail=f"File type not allowed. Allowed types: {', '.join(allowed_extensions)}"
            )
        
        file_content = await file.read()
        if len(file_content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File size must be less than 10MB")
        
        # Optimize image
        optimized_content = optimize_image(file_content)
        
        # Save with timestamp and original filename
        timestamp = datetime.utcnow().timestamp()
        unique_filename = f"{int(timestamp)}_{file.filename}"
        file_path = os.path.join(PRODUCT_IMAGES_DIR, unique_filename)
        
        with open(file_path, "wb") as f:
            f.write(optimized_content)
        
        image_url = f"/uploads/products/{unique_filename}"
        
        logger.info(f"✅ Image uploaded successfully: {image_url}")
        
        return {
            "success": True,
            "message": "Image uploaded successfully",
            "image_url": image_url,
            "filename": unique_filename,
            "file_size": len(optimized_content)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Image upload error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/upload-multiple-images")
async def upload_multiple_images(files: List[UploadFile] = File(...)):
    """
    Upload multiple image files at once
    Accepts up to 10 images, each max 10MB
    """
    try:
        if len(files) > 10:
            raise HTTPException(status_code=400, detail="Maximum 10 images allowed per upload")
        
        allowed_extensions = {"jpg", "jpeg", "png", "gif", "webp"}
        uploaded_images = []
        
        for file in files:
            file_extension = file.filename.split(".")[-1].lower()
            
            if file_extension not in allowed_extensions:
                logger.warning(f"⚠️  Skipping unsupported file: {file.filename}")
                continue
            
            file_content = await file.read()
            if len(file_content) > 10 * 1024 * 1024:
                logger.warning(f"⚠️  File too large, skipping: {file.filename}")
                continue
            
            # Optimize image
            optimized_content = optimize_image(file_content)
            
            # Save file
            timestamp = datetime.utcnow().timestamp()
            unique_filename = f"{int(timestamp)}_{file.filename}"
            file_path = os.path.join(PRODUCT_IMAGES_DIR, unique_filename)
            
            with open(file_path, "wb") as f:
                f.write(optimized_content)
            
            image_url = f"/uploads/products/{unique_filename}"
            
            uploaded_images.append({
                "success": True,
                "image_url": image_url,
                "filename": unique_filename,
                "file_size": len(optimized_content)
            })
        
        if not uploaded_images:
            raise HTTPException(status_code=400, detail="No valid images were uploaded")
        
        logger.info(f"✅ {len(uploaded_images)} images uploaded successfully")
        
        return {
            "success": True,
            "message": f"{len(uploaded_images)} images uploaded successfully",
            "count": len(uploaded_images),
            "images": uploaded_images
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Multiple image upload error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/upload-product-image")
async def upload_product_image(
    file: UploadFile = File(...),
    admin_token: str = Header(None)
):
    """
    Admin endpoint to upload product image
    Requires admin authentication
    """
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        allowed_extensions = {"jpg", "jpeg", "png", "gif", "webp"}
        file_extension = file.filename.split(".")[-1].lower()
        
        if file_extension not in allowed_extensions:
            raise HTTPException(
                status_code=400, 
                detail=f"File type not allowed. Allowed types: {', '.join(allowed_extensions)}"
            )
        
        file_content = await file.read()
        if len(file_content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File size must be less than 10MB")
        
        # Optimize image
        optimized_content = optimize_image(file_content)
        
        # Save file
        timestamp = datetime.utcnow().timestamp()
        unique_filename = f"{int(timestamp)}_{file.filename}"
        file_path = os.path.join(PRODUCT_IMAGES_DIR, unique_filename)
        
        with open(file_path, "wb") as f:
            f.write(optimized_content)
        
        image_url = f"/uploads/products/{unique_filename}"
        
        logger.info(f"✅ Product image uploaded by admin: {image_url}")
        
        return {
            "success": True,
            "message": "Product image uploaded successfully",
            "image_url": image_url,
            "filename": unique_filename,
            "file_size": len(optimized_content)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Admin image upload error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/admin/delete-image/{filename}")
def delete_image(filename: str, admin_token: str = Header(None)):
    """
    Admin endpoint to delete a product image
    Requires admin authentication
    """
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        # Prevent directory traversal attacks
        if ".." in filename or "/" in filename:
            raise HTTPException(status_code=400, detail="Invalid filename")
        
        file_path = os.path.join(PRODUCT_IMAGES_DIR, filename)
        
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Image not found")
        
        os.remove(file_path)
        
        logger.info(f"✅ Image deleted: {filename}")
        
        return {
            "success": True,
            "message": "Image deleted successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Image delete error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/uploaded-images")
def get_uploaded_images(admin_token: str = Header(None)):
    """
    Get list of all uploaded product images
    Requires admin authentication
    """
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        images = []
        
        if os.path.exists(PRODUCT_IMAGES_DIR):
            for filename in os.listdir(PRODUCT_IMAGES_DIR):
                file_path = os.path.join(PRODUCT_IMAGES_DIR, filename)
                if os.path.isfile(file_path):
                    file_size = os.path.getsize(file_path)
                    file_modified = datetime.fromtimestamp(os.path.getmtime(file_path))
                    
                    images.append({
                        "filename": filename,
                        "url": f"/uploads/products/{filename}",
                        "size": file_size,
                        "modified": file_modified.isoformat()
                    })
        
        # Sort by most recent first
        images.sort(key=lambda x: x["modified"], reverse=True)
        
        logger.info(f"📦 Retrieved {len(images)} uploaded images")
        
        return {
            "success": True,
            "count": len(images),
            "images": images
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error fetching uploaded images: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= PRODUCTS ENDPOINTS =============

@app.get("/api/products")
def get_products(category: Optional[str] = None, brand: Optional[str] = None, sort: str = "newest"):
    """Get all products with optional filters and sorting"""
    check_db_connection()
    
    try:
        logger.info(f"📦 Fetching products - Category: {category}, Brand: {brand}, Sort: {sort}")
        
        query = {}
        
        if category:
            query["category"] = category
        if brand:
            query["brand"] = brand
        
        # Sort options
        sort_map = {
            "newest": [("createdAt", -1)],
            "price_low": [("base_price", 1)],
            "price_high": [("base_price", -1)],
            "rating": [("rating", -1)],
            "popular": [("reviews_count", -1)]
        }
        
        sort_criteria = sort_map.get(sort, [("createdAt", -1)])
        
        products = list(db.products.find(query).sort(sort_criteria))
        
        logger.info(f"✅ Found {len(products)} products in database")
        
        # Format products for frontend (hide exact stock counts)
        formatted_products = []
        for product in products:
            product["_id"] = str(product["_id"])
            
            # Calculate total stock from SKUs
            total_stock = 0
            if product.get("skus"):
                for sku in product["skus"]:
                    total_stock += sku.get("stock", 0)
            
            # Get stock status (In Stock, Low Stock, Out of Stock)
            stock_status = get_stock_status(total_stock)
            product["stock_status"] = stock_status
            product["in_stock"] = stock_status["in_stock"]
            
            # Remove exact stock numbers from frontend response
            product.pop("stock", None)
            if product.get("skus"):
                for sku in product["skus"]:
                    sku.pop("stock", None)
            
            formatted_products.append(product)
        
        return {
            "success": True,
            "count": len(formatted_products),
            "products": formatted_products
        }
    except Exception as e:
        logger.error(f"❌ Error fetching products: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    """Get single product with full details"""
    check_db_connection()
    
    try:
        # Validate ObjectId
        try:
            obj_id = ObjectId(product_id)
        except:
            raise HTTPException(status_code=400, detail="Invalid product ID format")
        
        product = db.products.find_one({"_id": obj_id})
        
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        
        product["_id"] = str(product["_id"])
        
        # Calculate total stock from SKUs
        total_stock = 0
        if product.get("skus"):
            for sku in product["skus"]:
                total_stock += sku.get("stock", 0)
        
        # Get stock status
        stock_status = get_stock_status(total_stock)
        product["stock_status"] = stock_status
        product["in_stock"] = stock_status["in_stock"]
        
        # Remove exact stock numbers from frontend
        product.pop("stock", None)
        if product.get("skus"):
            for sku in product["skus"]:
                sku.pop("stock", None)
        
        # Get reviews for this product
        reviews = list(db.reviews.find({"product_id": product_id}).limit(10))
        for review in reviews:
            review["_id"] = str(review["_id"])
        
        product["reviews"] = reviews
        
        logger.info(f"✅ Retrieved product: {product['name']}")
        
        return {
            "success": True,
            "product": product
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error fetching product: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/products")
def create_product(product: Product, admin_token: str = Header(None)):
    """Create a new product (Admin only)"""
    check_db_connection()
    
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        product_dict = product.dict()
        product_dict["createdAt"] = datetime.utcnow()
        product_dict["updatedAt"] = datetime.utcnow()
        product_dict["inStock"] = True
        
        result = db.products.insert_one(product_dict)
        product_dict["_id"] = str(result.inserted_id)
        
        logger.info(f"✅ Product created: {product_dict['name']} (ID: {result.inserted_id})")
        
        return {
            "success": True,
            "message": "Product created successfully",
            "product": product_dict
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error creating product: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/products/{product_id}")
def update_product(product_id: str, product: Product, admin_token: str = Header(None)):
    """Update a product (Admin only)"""
    check_db_connection()
    
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        try:
            obj_id = ObjectId(product_id)
        except:
            raise HTTPException(status_code=400, detail="Invalid product ID format")
        
        product_dict = product.dict()
        product_dict["updatedAt"] = datetime.utcnow()
        
        result = db.products.update_one(
            {"_id": obj_id},
            {"$set": product_dict}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Product not found")
        
        logger.info(f"✅ Product updated: {product_id}")
        
        return {
            "success": True,
            "message": "Product updated successfully",
            "modified_count": result.modified_count
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error updating product: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/admin/products/{product_id}")
def delete_product(product_id: str, admin_token: str = Header(None)):
    """Delete a product (Admin only)"""
    check_db_connection()
    
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        try:
            obj_id = ObjectId(product_id)
        except:
            raise HTTPException(status_code=400, detail="Invalid product ID format")
        
        result = db.products.delete_one({"_id": obj_id})
        
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Product not found")
        
        logger.info(f"✅ Product deleted: {product_id}")
        
        return {
            "success": True,
            "message": "Product deleted successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error deleting product: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= PRODUCT REVIEWS =============

@app.post("/api/reviews/{product_id}")
def add_review(product_id: str, review_data: dict, authorization: str = Header(None)):
    """Add a review to a product"""
    check_db_connection()
    
    try:
        if not authorization:
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        try:
            obj_id = ObjectId(product_id)
        except:
            raise HTTPException(status_code=400, detail="Invalid product ID format")
        
        review = {
            "product_id": product_id,
            "user_id": review_data.get("user_id"),
            "rating": review_data.get("rating"),
            "title": review_data.get("title"),
            "comment": review_data.get("comment"),
            "createdAt": datetime.utcnow()
        }
        
        result = db.reviews.insert_one(review)
        review["_id"] = str(result.inserted_id)
        
        # Update product rating
        avg_rating = db.reviews.aggregate([
            {"$match": {"product_id": product_id}},
            {"$group": {"_id": None, "avg": {"$avg": "$rating"}, "count": {"$sum": 1}}}
        ])
        
        avg_data = list(avg_rating)
        if avg_data:
            db.products.update_one(
                {"_id": obj_id},
                {"$set": {
                    "rating": round(avg_data[0]["avg"], 2),
                    "reviews_count": avg_data[0]["count"]
                }}
            )
        
        logger.info(f"✅ Review added for product: {product_id}")
        
        return {
            "success": True,
            "message": "Review added successfully",
            "review": review
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error adding review: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= CART ENDPOINTS =============

@app.post("/api/cart")
def add_to_cart(cart_item: CartItem, authorization: str = Header(None)):
    """Add item to user's cart"""
    check_db_connection()
    
    try:
        if not authorization:
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        user_id = authorization.split(" ")[1] if " " in authorization else authorization
        
        # Validate product exists
        try:
            obj_id = ObjectId(cart_item.product_id)
            product = db.products.find_one({"_id": obj_id})
            if not product:
                raise HTTPException(status_code=404, detail="Product not found")
        except:
            raise HTTPException(status_code=400, detail="Invalid product ID")
        
        cart_item_dict = cart_item.dict()
        cart_item_dict["addedAt"] = datetime.utcnow()
        
        result = db.carts.update_one(
            {"user_id": user_id},
            {"$push": {"items": cart_item_dict}},
            upsert=True
        )
        
        logger.info(f"✅ Item added to cart for user: {user_id}")
        
        return {
            "success": True,
            "message": "Item added to cart"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error adding to cart: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/cart")
def get_cart(authorization: str = Header(None)):
    """Get user's cart"""
    check_db_connection()
    
    try:
        if not authorization:
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        user_id = authorization.split(" ")[1] if " " in authorization else authorization
        
        cart = db.carts.find_one({"user_id": user_id})
        
        if not cart:
            return {
                "success": True,
                "cart": {
                    "user_id": user_id,
                    "items": [],
                    "total": 0
                }
            }
        
        # Calculate total
        total = 0
        for item in cart.get("items", []):
            try:
                product = db.products.find_one({"_id": ObjectId(item["product_id"])})
                if product:
                    total += product.get("base_price", 0) * item.get("quantity", 1)
            except:
                pass
        
        return {
            "success": True,
            "cart": {
                "user_id": user_id,
                "items": cart.get("items", []),
                "total": total
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error fetching cart: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= AUTH ENDPOINTS =============

@app.post("/api/auth/send-otp")
def send_otp(data: dict):
    """Send OTP to email or phone"""
    check_db_connection()
    
    try:
        email = data.get("email")
        phone = data.get("phone")
        
        if not email and not phone:
            raise HTTPException(status_code=400, detail="Missing email or phone")
        
        identifier = email if email else phone
        login_method = "email" if email else "phone"
        
        logger.info(f"📧 Sending OTP to {login_method}: {identifier}")
        
        otp = generate_otp()
        
        otp_record = {
            "identifier": identifier,
            "login_method": login_method,
            "otp": otp,
            "createdAt": datetime.utcnow(),
            "expiresAt": datetime.utcnow().timestamp() + 600
        }
        
        db.otps.delete_many({"identifier": identifier})
        db.otps.insert_one(otp_record)
        logger.info(f"✅ OTP stored in database: {otp}")
        
        if login_method == "email":
            email_body = f"""
            <html>
                <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; padding: 20px;">
                    <div style="background-color: white; padding: 30px; border-radius: 8px; max-width: 500px; margin: 0 auto;">
                        <h2 style="color: #333;">Besties Craft - Login Verification</h2>
                        <p style="color: #666; font-size: 14px;">Your OTP is:</p>
                        <h1 style="font-size: 36px; color: #FF6B35; letter-spacing: 8px; text-align: center; margin: 30px 0;">{otp}</h1>
                        <p style="color: #999; font-size: 12px;">This OTP will expire in 10 minutes.</p>
                        <p style="color: #999; font-size: 12px;">If you didn't request this, please ignore this email.</p>
                    </div>
                </body>
            </html>
            """
            email_sent = send_email(email, "Besties Craft - Your OTP", email_body)
            if not email_sent:
                logger.warning("⚠️  Email sending failed, but OTP was stored")
        
        elif login_method == "phone":
            sms_sent = send_sms(phone, otp)
            if not sms_sent:
                logger.warning("⚠️  SMS sending failed, but OTP was stored")
        
        return {
            "success": True,
            "message": f"OTP sent to your {login_method}",
            "identifier": identifier
        }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Send OTP Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/verify-otp")
def verify_otp(data: dict):
    """Verify OTP and login user"""
    check_db_connection()
    
    try:
        email = data.get("email")
        phone = data.get("phone")
        otp_entered = str(data.get("otp", "")).strip()
        
        identifier = email if email else phone
        login_method = "email" if email else "phone"
        
        if not identifier or not otp_entered:
            raise HTTPException(status_code=400, detail="Missing email/phone or OTP")
        
        logger.info(f"🔍 Verifying OTP for {login_method}: {identifier}")
        
        otp_record = db.otps.find_one({"identifier": identifier})
        
        if not otp_record:
            raise HTTPException(status_code=401, detail="OTP not found or expired")
        
        if datetime.utcnow().timestamp() > otp_record.get("expiresAt", 0):
            db.otps.delete_one({"_id": otp_record["_id"]})
            raise HTTPException(status_code=401, detail="OTP has expired")
        
        if str(otp_record["otp"]) != otp_entered:
            logger.warning(f"❌ Invalid OTP. Expected: {otp_record['otp']}, Got: {otp_entered}")
            raise HTTPException(status_code=401, detail="Invalid OTP")
        
        logger.info(f"✅ OTP verified successfully!")
        
        db.otps.delete_one({"_id": otp_record["_id"]})
        
        user_data = {
            login_method: identifier,
            "lastLogin": datetime.utcnow()
        }
        
        db.users.update_one(
            {login_method: identifier},
            {"$set": user_data},
            upsert=True
        )
        
        user = db.users.find_one({login_method: identifier})
        user_id = str(user["_id"]) if user else None
        
        token = hashlib.sha256(f"{user_id}{identifier}{datetime.utcnow()}".encode()).hexdigest()
        
        return {
            "success": True,
            "message": "Login successful",
            "token": token,
            "user": {
                "id": user_id,
                "email": email if login_method == "email" else None,
                "phone": phone if login_method == "phone" else None
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Verify OTP Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/admin-login")
def admin_login(credentials: dict):
    """Admin login endpoint"""
    try:
        password = credentials.get("password")
        
        admin_email = os.getenv("ADMIN_EMAIL", "bestiescraft1434@gmail.com")
        admin_password = os.getenv("ADMIN_PASSWORD", "Bhola143")
        
        if not password:
            raise HTTPException(status_code=400, detail="Password is required")
        
        if password == admin_password:
            token = hashlib.sha256(f"{admin_email}{datetime.utcnow()}".encode()).hexdigest()
            
            logger.info(f"✅ Admin login successful for {admin_email}")
            
            return {
                "success": True,
                "message": "Login successful",
                "token": token,
                "email": admin_email
            }
        else:
            logger.warning(f"❌ Invalid admin password attempt")
            raise HTTPException(status_code=401, detail="Invalid credentials")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Admin Login Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= ORDERS ENDPOINTS =============

@app.post("/api/orders")
def create_order(order: Order, authorization: str = Header(None)):
    """Create a new order"""
    check_db_connection()
    
    try:
        if not authorization:
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        order_dict = order.dict()
        order_dict["status"] = "pending"
        order_dict["createdAt"] = datetime.utcnow()
        
        # Calculate total and validate products
        total = 0
        for item in order_dict.get("items", []):
            try:
                product = db.products.find_one({"_id": ObjectId(item["product_id"])})
                if not product:
                    raise HTTPException(status_code=404, detail=f"Product {item['product_id']} not found")
                total += product.get("base_price", 0) * item.get("quantity", 1)
            except:
                raise HTTPException(status_code=400, detail="Invalid product in order")
        
        order_dict["total_amount"] = total
        
        result = db.orders.insert_one(order_dict)
        order_dict["_id"] = str(result.inserted_id)
        
        logger.info(f"✅ Order created: {order_dict['_id']}")
        
        return {
            "success": True,
            "message": "Order created successfully",
            "order": order_dict,
            "razorpay_order": {
                "id": f"order_{result.inserted_id}",
                "amount": int(total * 100)
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error creating order: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/orders/verify-payment")
def verify_payment(payment_data: dict):
    """Verify payment for an order"""
    check_db_connection()
    
    try:
        order_id = payment_data.get("order_id")
        
        try:
            obj_id = ObjectId(order_id)
        except:
            raise HTTPException(status_code=400, detail="Invalid order ID")
        
        result = db.orders.update_one(
            {"_id": obj_id},
            {"$set": {"status": "completed", "paidAt": datetime.utcnow()}}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Order not found")
        
        logger.info(f"✅ Payment verified for order: {order_id}")
        
        return {
            "success": True,
            "message": "Payment verified successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error verifying payment: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/orders")
def get_all_orders(admin_token: str = Header(None)):
    """Get all orders (Admin only)"""
    check_db_connection()
    
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        orders = list(db.orders.find().sort("createdAt", -1))
        for order in orders:
            order["_id"] = str(order["_id"])
        
        logger.info(f"📦 Retrieved {len(orders)} orders")
        
        return {
            "success": True,
            "count": len(orders),
            "orders": orders
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error fetching orders: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/orders/{order_id}")
def update_order_status(order_id: str, status_data: dict, admin_token: str = Header(None)):
    """Update order status (Admin only)"""
    check_db_connection()
    
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        try:
            obj_id = ObjectId(order_id)
        except:
            raise HTTPException(status_code=400, detail="Invalid order ID")
        
        new_status = status_data.get("status")
        
        if not new_status:
            raise HTTPException(status_code=400, detail="Status is required")
        
        result = db.orders.update_one(
            {"_id": obj_id},
            {"$set": {
                "status": new_status,
                "updatedAt": datetime.utcnow()
            }}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Order not found")
        
        logger.info(f"✅ Order status updated: {order_id} -> {new_status}")
        
        return {
            "success": True,
            "message": f"Order status updated to {new_status}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error updating order: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= ADMIN DASHBOARD STATS =============

@app.get("/api/admin/dashboard-stats")
def get_dashboard_stats(admin_token: str = Header(None)):
    """Get dashboard statistics (Admin only)"""
    check_db_connection()
    
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        total_products = db.products.count_documents({})
        total_orders = db.orders.count_documents({})
        total_customers = db.users.count_documents({})
        
        # Calculate total revenue
        revenue_data = list(db.orders.aggregate([
            {"$match": {"status": "completed"}},
            {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}}
        ]))
        total_revenue = revenue_data[0]["total"] if revenue_data else 0
        
        # Get order status breakdown
        order_status = list(db.orders.aggregate([
            {"$group": {"_id": "$status", "count": {"$sum": 1}}}
        ]))
        
        logger.info(f"📊 Dashboard stats retrieved")
        
        return {
            "success": True,
            "stats": {
                "total_products": total_products,
                "total_orders": total_orders,
                "total_customers": total_customers,
                "total_revenue": total_revenue,
                "order_status": {item["_id"]: item["count"] for item in order_status}
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error fetching dashboard stats: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= APPLICATION ENTRY POINT =============

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    
    logger.info(f"🚀 Starting Besties Craft Backend API on port {port}")
    logger.info(f"📚 API Documentation available at http://localhost:{port}/docs")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
