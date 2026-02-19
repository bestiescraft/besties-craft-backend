from fastapi import FastAPI, HTTPException, Header, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
import os
from pathlib import Path
import random

# ============= SETUP =============

UPLOAD_DIR = "uploads"
Path(UPLOAD_DIR).mkdir(exist_ok=True)

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "besties_craft_db")

# Connect to MongoDB
client = MongoClient(MONGO_URI)
db = client[DATABASE_NAME]

app = FastAPI(title="Besties Craft API", version="2.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# ============= MODELS =============

class ProductImage(BaseModel):
    url: str
    alt_text: Optional[str] = None
    is_primary: bool = False

class SKUOption(BaseModel):
    variant_values: dict
    sku: str
    price: float
    stock: int
    weight: Optional[float] = None

class ProductVariant(BaseModel):
    name: str
    options: List[str]
    is_visible: bool = True

class Product(BaseModel):
    name: str
    description: str
    base_price: float
    images: List[ProductImage]
    category: str = "general"
    stock: int = 0
    # ✅ FIX 3: Added colors field — was missing, so colors were being dropped on save!
    colors: List[str] = []
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

# ============= BASIC ENDPOINTS =============

@app.get("/")
def root():
    return {
        "message": "Besties Craft Backend API",
        "version": "2.0",
        "docs": "/docs"
    }

@app.get("/health")
def health_check():
    try:
        db.admin.command('ping')
        product_count = db.products.count_documents({})
        return {
            "status": "ok",
            "database": "connected",
            "products_count": product_count
        }
    except:
        return {
            "status": "error",
            "database": "disconnected",
            "products_count": 0
        }

# ============= FILE UPLOAD =============

# ✅ FIX 2: Upload now returns a FULL absolute URL using the backend base URL
# so images don't break when frontend is on a different domain (localhost vs Render)
@app.post("/api/upload-image")
async def upload_image(file: UploadFile = File(...)):
    try:
        allowed_extensions = {"jpg", "jpeg", "png", "gif", "webp"}
        file_extension = file.filename.split(".")[-1].lower()
        
        if file_extension not in allowed_extensions:
            raise HTTPException(status_code=400, detail="File type not allowed")
        
        file_content = await file.read()
        if len(file_content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large")
        
        timestamp = datetime.utcnow().timestamp()
        unique_filename = f"{int(timestamp)}_{file.filename}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        
        with open(file_path, "wb") as f:
            f.write(file_content)

        # Build absolute URL so frontend can always reach the image
        backend_url = os.getenv("BACKEND_URL", "https://besties-craft-backend-1.onrender.com")
        full_url = f"{backend_url}/uploads/{unique_filename}"
        
        return {
            "success": True,
            "image_url": full_url,   # ← full URL now, not just /uploads/...
            "filename": unique_filename
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= PRODUCTS ENDPOINTS =============

@app.get("/api/admin/products")
def get_admin_products(admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized - No token provided")
        
        products = list(db.products.find())
        for product in products:
            product["_id"] = str(product["_id"])
        
        return {
            "success": True,
            "products": products
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products")
def get_products(category: Optional[str] = None, brand: Optional[str] = None, sort: str = "newest"):
    try:
        query = {}
        
        if category:
            query["category"] = category
        if brand:
            query["brand"] = brand
        
        sort_map = {
            "newest": [("createdAt", -1)],
            "price_low": [("base_price", 1)],
            "price_high": [("base_price", -1)],
            "rating": [("rating", -1)],
            "popular": [("reviews_count", -1)]
        }
        
        sort_criteria = sort_map.get(sort, [("createdAt", -1)])
        products = list(db.products.find(query).sort(sort_criteria))
        
        for product in products:
            product["_id"] = str(product["_id"])
            product["in_stock"] = product.get("stock", 0) > 0
            if product.get("skus"):
                for sku in product["skus"]:
                    sku.pop("stock", None)
        
        return {
            "success": True,
            "count": len(products),
            "products": products
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    try:
        product = db.products.find_one({"_id": ObjectId(product_id)})
        
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        
        product["_id"] = str(product["_id"])
        product["in_stock"] = product.get("stock", 0) > 0
        
        reviews = list(db.reviews.find({"product_id": product_id}).limit(10))
        for review in reviews:
            review["_id"] = str(review["_id"])
        
        product["reviews"] = reviews
        
        return {
            "success": True,
            "product": product
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= ADMIN PRODUCTS =============

@app.post("/api/admin/products")
def create_product(product: Product, admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized - No token provided")
        
        product_dict = product.dict()
        product_dict["createdAt"] = datetime.utcnow()
        product_dict["updatedAt"] = datetime.utcnow()
        
        result = db.products.insert_one(product_dict)
        product_dict["_id"] = str(result.inserted_id)
        
        return {
            "success": True,
            "message": "Product created",
            "product": product_dict
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/products/{product_id}")
def update_product(product_id: str, product: Product, admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized - No token provided")
        
        product_dict = product.dict()
        product_dict["updatedAt"] = datetime.utcnow()
        
        result = db.products.update_one(
            {"_id": ObjectId(product_id)},
            {"$set": product_dict}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Product not found")
        
        return {
            "success": True,
            "message": "Product updated"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/admin/products/{product_id}")
def delete_product(product_id: str, admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized - No token provided")
        
        result = db.products.delete_one({"_id": ObjectId(product_id)})
        
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Product not found")
        
        return {
            "success": True,
            "message": "Product deleted"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= REVIEWS =============

@app.post("/api/reviews/{product_id}")
def add_review(product_id: str, review_data: dict, authorization: str = Header(None)):
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
        
        return {
            "success": True,
            "review": review
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= CART =============

@app.post("/api/cart")
def add_to_cart(cart_item: CartItem, authorization: str = Header(None)):
    try:
        if not authorization:
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        user_id = authorization.split(" ")[1] if " " in authorization else authorization
        
        product = db.products.find_one({"_id": ObjectId(cart_item.product_id)})
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        
        cart_item_dict = cart_item.dict()
        cart_item_dict["addedAt"] = datetime.utcnow()
        
        db.carts.update_one(
            {"user_id": user_id},
            {"$push": {"items": cart_item_dict}},
            upsert=True
        )
        
        return {"success": True, "message": "Item added to cart"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/cart")
def get_cart(authorization: str = Header(None)):
    try:
        if not authorization:
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        user_id = authorization.split(" ")[1] if " " in authorization else authorization
        
        cart = db.carts.find_one({"user_id": user_id})
        
        if not cart:
            return {
                "success": True,
                "cart": {"user_id": user_id, "items": [], "total": 0}
            }
        
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
        raise HTTPException(status_code=500, detail=str(e))

# ============= AUTH =============

@app.post("/api/auth/send-otp")
def send_otp(data: dict):
    try:
        email = data.get("email")
        phone = data.get("phone")
        
        if not email and not phone:
            raise HTTPException(status_code=400, detail="Email or phone required")
        
        identifier = email if email else phone
        
        otp = ''.join([str(random.randint(0, 9)) for _ in range(6)])
        
        otp_record = {
            "identifier": identifier,
            "otp": otp,
            "createdAt": datetime.utcnow(),
            "expiresAt": datetime.utcnow().timestamp() + 600
        }
        
        db.otps.delete_many({"identifier": identifier})
        db.otps.insert_one(otp_record)
        
        return {
            "success": True,
            "message": "OTP sent",
            "identifier": identifier
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/verify-otp")
def verify_otp(data: dict):
    try:
        email = data.get("email")
        phone = data.get("phone")
        otp_entered = str(data.get("otp", "")).strip()
        
        identifier = email if email else phone
        
        if not identifier or not otp_entered:
            raise HTTPException(status_code=400, detail="Missing data")
        
        otp_record = db.otps.find_one({"identifier": identifier})
        
        if not otp_record:
            raise HTTPException(status_code=401, detail="OTP expired")
        
        if datetime.utcnow().timestamp() > otp_record.get("expiresAt", 0):
            db.otps.delete_one({"_id": otp_record["_id"]})
            raise HTTPException(status_code=401, detail="OTP expired")
        
        if str(otp_record["otp"]) != otp_entered:
            raise HTTPException(status_code=401, detail="Invalid OTP")
        
        db.otps.delete_one({"_id": otp_record["_id"]})
        
        user_data = {
            "email": email if email else None,
            "phone": phone if phone else None,
            "lastLogin": datetime.utcnow()
        }
        
        db.users.update_one(
            {"$or": [{"email": email}, {"phone": phone}]},
            {"$set": user_data},
            upsert=True
        )
        
        user = db.users.find_one({"$or": [{"email": email}, {"phone": phone}]})
        user_id = str(user["_id"]) if user else None
        
        token = __import__('hashlib').sha256(f"{user_id}{identifier}{datetime.utcnow()}".encode()).hexdigest()
        
        return {
            "success": True,
            "token": token,
            "user": {
                "id": user_id,
                "email": email,
                "phone": phone
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/admin-login")
def admin_login(credentials: dict):
    try:
        password = credentials.get("password")
        
        if not password:
            raise HTTPException(status_code=400, detail="Password required")
        
        admin_password = os.getenv("ADMIN_PASSWORD", "Bhola143")
        
        if password == admin_password:
            admin_email = os.getenv("ADMIN_EMAIL", "bestiescraft1434@gmail.com")
            token = __import__('hashlib').sha256(f"{admin_email}{datetime.utcnow()}".encode()).hexdigest()
            
            return {
                "success": True,
                "token": token,
                "email": admin_email
            }
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= ORDERS =============

@app.post("/api/orders")
def create_order(order: Order, authorization: str = Header(None)):
    try:
        if not authorization:
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        order_dict = order.dict()
        order_dict["status"] = "pending"
        order_dict["createdAt"] = datetime.utcnow()
        
        total = 0
        for item in order_dict.get("items", []):
            product = db.products.find_one({"_id": ObjectId(item["product_id"])})
            if not product:
                raise HTTPException(status_code=404, detail="Product not found")
            total += product.get("base_price", 0) * item.get("quantity", 1)
        
        order_dict["total_amount"] = total
        
        result = db.orders.insert_one(order_dict)
        order_dict["_id"] = str(result.inserted_id)
        
        return {
            "success": True,
            "order": order_dict,
            "razorpay_order": {
                "id": f"order_{result.inserted_id}",
                "amount": int(total * 100)
            }
        }
    except HTTPException:
        raise
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
        
        return {"success": True, "message": "Payment verified"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= ADMIN ORDERS =============

@app.get("/api/admin/orders")
def get_all_orders(admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized - No token provided")
        
        orders = list(db.orders.find().sort("createdAt", -1))
        for order in orders:
            order["_id"] = str(order["_id"])
        
        return {
            "success": True,
            "count": len(orders),
            "orders": orders
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/orders/{order_id}")
def update_order_status(order_id: str, status_data: dict, admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized - No token provided")
        
        new_status = status_data.get("status")
        
        result = db.orders.update_one(
            {"_id": ObjectId(order_id)},
            {"$set": {"status": new_status, "updatedAt": datetime.utcnow()}}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Order not found")
        
        return {"success": True, "message": "Order updated"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= DASHBOARD =============

@app.get("/api/admin/dashboard-stats")
def get_dashboard_stats(admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized - No token provided")
        
        total_products = db.products.count_documents({})
        total_orders = db.orders.count_documents({})
        total_customers = db.users.count_documents({})
        
        revenue_data = list(db.orders.aggregate([
            {"$match": {"status": "completed"}},
            {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}}
        ]))
        total_revenue = revenue_data[0]["total"] if revenue_data else 0
        
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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= RUN =============

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
