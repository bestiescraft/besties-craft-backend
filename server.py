from fastapi import FastAPI, HTTPException, Header, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
import os
import random
import string
import requests
import hashlib
from pathlib import Path

# Create uploads directory if it doesn't exist
UPLOAD_DIR = "uploads"
Path(UPLOAD_DIR).mkdir(exist_ok=True)

# MongoDB Connection
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "besties_craft_db")

try:
    client = MongoClient(MONGO_URI)
    db = client[DATABASE_NAME]
    print("✅ MongoDB connected successfully")
except Exception as e:
    print(f"❌ MongoDB connection failed: {e}")

# ============= PYDANTIC MODELS =============

class ProductVariant(BaseModel):
    """Variant options like color, size, etc."""
    name: str  # e.g., "Color", "Size"
    options: List[str]  # e.g., ["Red", "Blue", "Green"]
    is_visible: bool = True

class ProductImage(BaseModel):
    """Product images"""
    url: str
    alt_text: Optional[str] = None
    is_primary: bool = False

class SKUOption(BaseModel):
    """Combination of variant options for a specific SKU"""
    variant_values: dict  # e.g., {"Color": "Red", "Size": "M"}
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
    selected_variants: Optional[dict] = None  # e.g., {"Color": "Red", "Size": "M"}

class Order(BaseModel):
    user_id: str
    items: List[CartItem]
    shipping_address: dict
    billing_address: Optional[dict] = None
    payment_method: str = "razorpay"

app = FastAPI()

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

# ============= HELPER FUNCTIONS =============

def generate_otp():
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

def send_email(recipient_email, subject, body):
    try:
        api_key = os.getenv("BREVO_API_KEY")
        
        if not api_key:
            print("❌ BREVO_API_KEY not found in environment variables")
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
            headers=headers
        )
        
        if response.status_code == 201:
            print(f"✅ Email sent successfully to {recipient_email}")
            return True
        else:
            print(f"❌ Email error: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Email error: {str(e)}")
        return False

def send_sms(phone_number, otp):
    try:
        from twilio.rest import Client
        
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        twilio_phone = os.getenv("TWILIO_PHONE_NUMBER")
        
        if not all([account_sid, auth_token, twilio_phone]):
            print("❌ Twilio credentials not found in environment variables")
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
        
        print(f"✅ SMS sent successfully to {phone_number}: {message.sid}")
        return True
        
    except Exception as e:
        print(f"❌ SMS error: {str(e)}")
        return False

# ============= BASIC ENDPOINTS =============

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/")
def root():
    return {
        "message": "Besties Craft Backend API",
        "docs": "/docs",
        "health": "/health",
        "version": "2.0"
    }

# ============= FILE UPLOAD ENDPOINT =============

@app.post("/api/upload-image")
async def upload_image(file: UploadFile = File(...)):
    """
    Upload an image file and return the URL
    Accepts: JPG, PNG, GIF, WebP (Max 5MB)
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
        if len(file_content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File size must be less than 5MB")
        
        timestamp = datetime.utcnow().timestamp()
        unique_filename = f"{int(timestamp)}_{file.filename}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        
        with open(file_path, "wb") as f:
            f.write(file_content)
        
        image_url = f"/uploads/{unique_filename}"
        
        print(f"✅ Image uploaded successfully: {image_url}")
        
        return {
            "success": True,
            "message": "Image uploaded successfully",
            "image_url": image_url,
            "filename": unique_filename
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Image upload error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= PRODUCTS ENDPOINTS =============

# GET all products with filters
@app.get("/api/products")
def get_products(category: Optional[str] = None, brand: Optional[str] = None, sort: str = "newest"):
    try:
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
        raise HTTPException(status_code=500, detail=str(e))

# GET single product with full details
@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    try:
        product = db.products.find_one({"_id": ObjectId(product_id)})
        
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
        
        return {
            "success": True,
            "product": product
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# CREATE product (Admin only)
@app.post("/api/admin/products")
def create_product(product: Product, admin_token: str = Header(None)):
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        product_dict = product.dict()
        product_dict["createdAt"] = datetime.utcnow()
        product_dict["updatedAt"] = datetime.utcnow()
        product_dict["inStock"] = True
        
        result = db.products.insert_one(product_dict)
        product_dict["_id"] = str(result.inserted_id)
        
        print(f"✅ Product created: {product_dict['name']}")
        
        return {
            "success": True,
            "message": "Product created successfully",
            "product": product_dict
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# UPDATE product (Admin only)
@app.put("/api/admin/products/{product_id}")
def update_product(product_id: str, product: Product, admin_token: str = Header(None)):
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        product_dict = product.dict()
        product_dict["updatedAt"] = datetime.utcnow()
        
        result = db.products.update_one(
            {"_id": ObjectId(product_id)},
            {"$set": product_dict}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Product not found")
        
        print(f"✅ Product updated: {product_id}")
        
        return {
            "success": True,
            "message": "Product updated successfully",
            "modified_count": result.modified_count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# DELETE product (Admin only)
@app.delete("/api/admin/products/{product_id}")
def delete_product(product_id: str, admin_token: str = Header(None)):
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        result = db.products.delete_one({"_id": ObjectId(product_id)})
        
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Product not found")
        
        print(f"✅ Product deleted: {product_id}")
        
        return {
            "success": True,
            "message": "Product deleted successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= PRODUCT REVIEWS =============

@app.post("/api/reviews/{product_id}")
def add_review(product_id: str, review_data: dict, authorization: str = Header(None)):
    """
    Add a review to a product
    """
    try:
        if not authorization:
            raise HTTPException(status_code=401, detail="Unauthorized")
        
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
                {"_id": ObjectId(product_id)},
                {"$set": {
                    "rating": round(avg_data[0]["avg"], 2),
                    "reviews_count": avg_data[0]["count"]
                }}
            )
        
        print(f"✅ Review added for product: {product_id}")
        
        return {
            "success": True,
            "message": "Review added successfully",
            "review": review
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= CART ENDPOINTS =============

@app.post("/api/cart")
def add_to_cart(cart_item: CartItem, authorization: str = Header(None)):
    """
    Add item to user's cart
    """
    try:
        if not authorization:
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        user_id = authorization.split(" ")[1] if " " in authorization else authorization
        
        cart_item_dict = cart_item.dict()
        cart_item_dict["addedAt"] = datetime.utcnow()
        
        result = db.carts.update_one(
            {"user_id": user_id},
            {"$push": {"items": cart_item_dict}},
            upsert=True
        )
        
        print(f"✅ Item added to cart for user: {user_id}")
        
        return {
            "success": True,
            "message": "Item added to cart"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/cart")
def get_cart(authorization: str = Header(None)):
    """
    Get user's cart
    """
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
            product = db.products.find_one({"_id": ObjectId(item["product_id"])})
            if product:
                total += product.get("base_price", 0) * item.get("quantity", 1)
        
        return {
            "success": True,
            "cart": {
                "user_id": user_id,
                "items": cart.get("items", []),
                "total": total
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= AUTH ENDPOINTS =============

@app.post("/api/auth/send-otp")
def send_otp(data: dict):
    try:
        email = data.get("email")
        phone = data.get("phone")
        
        if not email and not phone:
            raise HTTPException(status_code=400, detail="Missing email or phone")
        
        identifier = email if email else phone
        login_method = "email" if email else "phone"
        
        print(f"📧 Sending OTP to {login_method}: {identifier}")
        
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
        print(f"✅ OTP stored in database: {otp}")
        
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
                raise HTTPException(status_code=500, detail="Failed to send email")
        
        elif login_method == "phone":
            sms_sent = send_sms(phone, otp)
            if not sms_sent:
                raise HTTPException(status_code=500, detail="Failed to send SMS")
        
        return {
            "success": True,
            "message": f"OTP sent to your {login_method}",
            "identifier": identifier
        }
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Send OTP Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/verify-otp")
def verify_otp(data: dict):
    try:
        email = data.get("email")
        phone = data.get("phone")
        otp_entered = str(data.get("otp", "")).strip()
        
        identifier = email if email else phone
        login_method = "email" if email else "phone"
        
        if not identifier or not otp_entered:
            raise HTTPException(status_code=400, detail="Missing email/phone or OTP")
        
        print(f"🔍 Verifying OTP for {login_method}: {identifier}")
        
        otp_record = db.otps.find_one({"identifier": identifier})
        
        if not otp_record:
            raise HTTPException(status_code=401, detail="OTP not found or expired")
        
        if datetime.utcnow().timestamp() > otp_record.get("expiresAt", 0):
            db.otps.delete_one({"_id": otp_record["_id"]})
            raise HTTPException(status_code=401, detail="OTP has expired")
        
        if str(otp_record["otp"]) != otp_entered:
            print(f"❌ Invalid OTP. Expected: {otp_record['otp']}, Got: {otp_entered}")
            raise HTTPException(status_code=401, detail="Invalid OTP")
        
        print(f"✅ OTP verified successfully!")
        
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
        print(f"❌ Verify OTP Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/admin-login")
def admin_login(credentials: dict):
    try:
        password = credentials.get("password")
        
        admin_email = os.getenv("ADMIN_EMAIL", "bestiescraft1434@gmail.com")
        admin_password = os.getenv("ADMIN_PASSWORD", "Bhola143")
        
        if not password:
            raise HTTPException(status_code=400, detail="Password is required")
        
        if password == admin_password:
            token = hashlib.sha256(f"{admin_email}{datetime.utcnow()}".encode()).hexdigest()
            
            print(f"✅ Admin login successful for {admin_email}")
            
            return {
                "success": True,
                "message": "Login successful",
                "token": token,
                "email": admin_email
            }
        else:
            print(f"❌ Invalid admin password attempt")
            raise HTTPException(status_code=401, detail="Invalid credentials")
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Admin Login Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= ORDERS ENDPOINTS =============

@app.post("/api/orders")
def create_order(order: Order, authorization: str = Header(None)):
    try:
        if not authorization:
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        order_dict = order.dict()
        order_dict["status"] = "pending"
        order_dict["createdAt"] = datetime.utcnow()
        
        # Calculate total
        total = 0
        for item in order_dict.get("items", []):
            product = db.products.find_one({"_id": ObjectId(item["product_id"])})
            if product:
                total += product.get("base_price", 0) * item.get("quantity", 1)
        
        order_dict["total_amount"] = total
        
        result = db.orders.insert_one(order_dict)
        order_dict["_id"] = str(result.inserted_id)
        
        print(f"✅ Order created: {order_dict['_id']}")
        
        return {
            "success": True,
            "message": "Order created successfully",
            "order": order_dict,
            "razorpay_order": {
                "id": f"order_{result.inserted_id}",
                "amount": int(total * 100)
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/orders/verify-payment")
def verify_payment(payment_data: dict):
    try:
        order_id = payment_data.get("order_id")
        
        db.orders.update_one(
            {"_id": ObjectId(order_id)},
            {"$set": {"status": "completed", "paidAt": datetime.utcnow()}}
        )
        
        print(f"✅ Payment verified for order: {order_id}")
        
        return {
            "success": True,
            "message": "Payment verified successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/admin/orders")
def get_all_orders(admin_token: str = Header(None)):
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        orders = list(db.orders.find().sort("createdAt", -1))
        for order in orders:
            order["_id"] = str(order["_id"])
        
        return {
            "success": True,
            "count": len(orders),
            "orders": orders
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/orders/{order_id}")
def update_order_status(order_id: str, status_data: dict, admin_token: str = Header(None)):
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        new_status = status_data.get("status")
        
        result = db.orders.update_one(
            {"_id": ObjectId(order_id)},
            {"$set": {
                "status": new_status,
                "updatedAt": datetime.utcnow()
            }}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Order not found")
        
        print(f"✅ Order status updated: {order_id} -> {new_status}")
        
        return {
            "success": True,
            "message": f"Order status updated to {new_status}"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= ADMIN DASHBOARD STATS =============

@app.get("/api/admin/dashboard-stats")
def get_dashboard_stats(admin_token: str = Header(None)):
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
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
