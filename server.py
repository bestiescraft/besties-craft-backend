from fastapi import FastAPI, HTTPException, Header, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, validator
from typing import List, Optional, Union
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime, timedelta
import os
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import cloudinary
import cloudinary.uploader
import firebase_admin
from firebase_admin import auth as fb_auth

# ============= SETUP =============

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "besties_craft_db")

client = MongoClient(MONGO_URI)
db = client[DATABASE_NAME]

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

# Firebase Admin SDK
_FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")
if _FIREBASE_PROJECT_ID and not firebase_admin._apps:
    try:
        firebase_admin.initialize_app(options={"projectId": _FIREBASE_PROJECT_ID})
    except Exception as _fe:
        print(f"Firebase Admin init warning: {_fe}")

# Gmail config (set these in Render environment variables)
GMAIL_USER = os.getenv("GMAIL_USER", "")          # e.g. bestiescraft1434@gmail.com
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")  # 16-char app password

app = FastAPI(title="Besties Craft API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============= HELPERS =============

def normalise_categories(raw) -> List[str]:
    if raw is None:
        return ["general"]
    if isinstance(raw, list):
        flat = []
        for item in raw:
            for part in str(item).split(","):
                part = part.strip()
                if part:
                    flat.append(part)
        return flat if flat else ["general"]
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return parts if parts else ["general"]
    return ["general"]

def fix_product_out(p: dict) -> dict:
    p["_id"] = str(p["_id"])
    raw = p.get("categories") or p.get("category")
    p["categories"] = normalise_categories(raw)
    p["category"] = p["categories"][0] if p["categories"] else "general"
    p["in_stock"] = p.get("stock", 0) > 0
    return p

def send_otp_email(to_email: str, otp: str):
    """Send OTP email via Gmail SMTP."""
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise Exception("Gmail credentials not configured in environment variables")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Besties Craft Login Code"
    msg["From"]    = f"Besties Craft <{GMAIL_USER}>"
    msg["To"]      = to_email

    html = f"""
    <div style="font-family:Georgia,serif;max-width:480px;margin:auto;padding:32px;background:#faf7f2;border-radius:16px;">
      <div style="text-align:center;margin-bottom:24px;">
        <div style="width:52px;height:52px;background:#1a1a1a;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;color:#d4a853;font-weight:bold;font-size:22px;">B</div>
        <div style="font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#888;margin-top:8px;">Artisan Collection</div>
        <div style="font-size:22px;font-weight:bold;color:#1a1a1a;">Besties <span style="color:#c0783c;">Craft</span></div>
      </div>
      <div style="background:#fff;border-radius:12px;padding:32px;text-align:center;">
        <h2 style="color:#1a1a1a;margin-bottom:8px;">Your Login Code</h2>
        <p style="color:#888;font-size:14px;margin-bottom:24px;">Enter this code on the login page. It expires in 10 minutes.</p>
        <div style="font-size:42px;font-weight:bold;letter-spacing:12px;color:#c0783c;background:#fdf9f0;padding:20px;border-radius:10px;margin-bottom:24px;">{otp}</div>
        <p style="color:#aaa;font-size:12px;">If you didn't request this, you can safely ignore this email.</p>
      </div>
    </div>
    """

    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, to_email, msg.as_string())

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
    categories: Optional[Union[List[str], str]] = None
    category:   Optional[Union[List[str], str]] = None
    stock: int = 0
    colors: List[str] = []
    variants: List[ProductVariant] = []
    skus: List[SKUOption] = []
    rating: float = 0
    reviews_count: int = 0
    brand: Optional[str] = None
    warranty: Optional[str] = None

    @validator("categories", pre=True, always=True)
    def normalise_cats(cls, v, values):
        raw = v if v is not None else values.get("category")
        return normalise_categories(raw)

class CartItem(BaseModel):
    product_id: str
    product_name: Optional[str] = None
    quantity: int
    price: Optional[float] = None
    color: Optional[str] = None
    customisation: Optional[str] = None
    selected_variants: Optional[dict] = None

class OrderItem(BaseModel):
    product_id: str
    product_name: Optional[str] = None
    quantity: int
    price: Optional[float] = None
    color: Optional[str] = None
    customisation: Optional[str] = None

class ShippingDetails(BaseModel):
    fullName:   Optional[str] = None
    email:      Optional[str] = None
    phone:      Optional[str] = None
    address:    Optional[str] = None
    city:       Optional[str] = None
    state:      Optional[str] = None
    postalCode: Optional[str] = None
    country:    Optional[str] = "India"

class CreateOrderRequest(BaseModel):
    user_id: str
    items: List[OrderItem]
    total_amount: float
    shipping_details: Optional[ShippingDetails] = None

class SendOTPRequest(BaseModel):
    email: str

class VerifyOTPRequest(BaseModel):
    email: str
    otp: str

# ============= BASIC ENDPOINTS =============

@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"message": "Besties Craft Backend API", "version": "2.0", "docs": "/docs"}

@app.api_route("/health", methods=["GET", "HEAD"])
def health_check():
    try:
        db.admin.command('ping')
        product_count = db.products.count_documents({})
        return {"status": "ok", "database": "connected", "products_count": product_count}
    except:
        return {"status": "error", "database": "disconnected", "products_count": 0}

# ============= EMAIL OTP AUTH =============

@app.post("/api/auth/send-otp")
def send_otp(req: SendOTPRequest):
    try:
        email = req.email.strip().lower()
        if not email or "@" not in email:
            raise HTTPException(status_code=400, detail="Invalid email address")

        # Generate 6-digit OTP
        otp = str(random.randint(100000, 999999))
        expires_at = datetime.utcnow() + timedelta(minutes=10)

        # Store OTP in MongoDB (upsert so only one OTP per email at a time)
        db.otps.update_one(
            {"email": email},
            {"$set": {"otp": otp, "expires_at": expires_at, "verified": False}},
            upsert=True
        )

        # Send email
        send_otp_email(email, otp)

        return {"success": True, "message": "OTP sent to your email"}

    except HTTPException:
        raise
    except Exception as e:
        print(f"Send OTP error: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to send OTP: {str(e)}")


@app.post("/api/auth/verify-otp")
def verify_otp(req: VerifyOTPRequest):
    try:
        email = req.email.strip().lower()
        otp   = req.otp.strip()

        record = db.otps.find_one({"email": email})

        if not record:
            raise HTTPException(status_code=400, detail="No OTP found for this email. Please request a new one.")

        if record.get("verified"):
            raise HTTPException(status_code=400, detail="OTP already used. Please request a new one.")

        if datetime.utcnow() > record["expires_at"]:
            db.otps.delete_one({"email": email})
            raise HTTPException(status_code=400, detail="OTP has expired. Please request a new one.")

        if record["otp"] != otp:
            raise HTTPException(status_code=400, detail="Incorrect OTP. Please try again.")

        # Mark as verified and clean up
        db.otps.delete_one({"email": email})

        # Upsert user in DB
        user_data = {
            "email":     email,
            "lastLogin": datetime.utcnow(),
        }
        db.users.update_one({"email": email}, {"$set": user_data}, upsert=True)
        user    = db.users.find_one({"email": email})
        user_id = str(user["_id"])

        return {
            "success": True,
            "message": "Login successful",
            "user": {
                "id":    user_id,
                "email": email,
                "name":  email.split("@")[0],
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Verify OTP error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= ONE-TIME MIGRATION =============

@app.post("/api/admin/migrate-categories")
def migrate_categories(admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized")
        products = list(db.products.find())
        updated = 0
        for p in products:
            raw_cats = p.get("categories")
            raw_cat  = p.get("category")
            if isinstance(raw_cats, list) and len(raw_cats) > 0:
                db.products.update_one(
                    {"_id": p["_id"]},
                    {"$set": {"categories": raw_cats, "category": raw_cats[0]}}
                )
            else:
                cats = normalise_categories(raw_cat or raw_cats)
                db.products.update_one(
                    {"_id": p["_id"]},
                    {"$set": {"categories": cats, "category": cats[0]}}
                )
            updated += 1
        return {"success": True, "message": f"Migrated {updated} products"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= FILE UPLOAD =============

@app.post("/api/upload-image")
async def upload_image(file: UploadFile = File(...)):
    try:
        allowed_extensions = {"jpg", "jpeg", "png", "gif", "webp"}
        file_extension = file.filename.split(".")[-1].lower()
        if file_extension not in allowed_extensions:
            raise HTTPException(status_code=400, detail="File type not allowed")
        file_content = await file.read()
        if len(file_content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large (max 10MB)")
        result = cloudinary.uploader.upload(
            file_content,
            folder="besties-craft-products",
            resource_type="image"
        )
        return {"success": True, "image_url": result["secure_url"], "filename": result["public_id"]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= PRODUCTS =============

@app.get("/api/admin/products")
def get_admin_products(admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized")
        products = list(db.products.find())
        for p in products:
            fix_product_out(p)
        return {"success": True, "products": products}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products")
def get_products(category: Optional[str] = None, brand: Optional[str] = None, sort: str = "newest"):
    try:
        query = {}
        if category:
            query["$or"] = [
                {"categories": {"$in": [category]}},
                {"category": category}
            ]
        if brand:
            query["brand"] = brand
        sort_map = {
            "newest":     [("createdAt", -1)],
            "price_low":  [("base_price", 1)],
            "price_high": [("base_price", -1)],
            "rating":     [("rating", -1)],
            "popular":    [("reviews_count", -1)]
        }
        products = list(db.products.find(query).sort(sort_map.get(sort, [("createdAt", -1)])))
        for p in products:
            fix_product_out(p)
        return {"success": True, "count": len(products), "products": products}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    try:
        product = db.products.find_one({"_id": ObjectId(product_id)})
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        fix_product_out(product)
        reviews = list(db.reviews.find({"product_id": product_id}).sort("createdAt", -1).limit(20))
        for r in reviews:
            r["_id"] = str(r["_id"])
        product["reviews"] = reviews
        return {"success": True, "product": product}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/products")
def create_product(product: Product, admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized")
        product_dict = product.dict()
        cats = normalise_categories(product_dict.get("categories") or product_dict.get("category"))
        product_dict["categories"] = cats
        product_dict["category"]   = cats[0]
        product_dict["createdAt"] = datetime.utcnow()
        product_dict["updatedAt"] = datetime.utcnow()
        result = db.products.insert_one(product_dict)
        product_dict["_id"] = str(result.inserted_id)
        return {"success": True, "message": "Product created", "product": product_dict}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/products/{product_id}")
def update_product(product_id: str, product: Product, admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized")
        product_dict = product.dict()
        cats = normalise_categories(product_dict.get("categories") or product_dict.get("category"))
        product_dict["categories"] = cats
        product_dict["category"]   = cats[0]
        product_dict["updatedAt"] = datetime.utcnow()
        result = db.products.update_one({"_id": ObjectId(product_id)}, {"$set": product_dict})
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Product not found")
        return {"success": True, "message": "Product updated"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/admin/products/{product_id}")
def delete_product(product_id: str, admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized")
        result = db.products.delete_one({"_id": ObjectId(product_id)})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Product not found")
        return {"success": True, "message": "Product deleted"}
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
            "product_id":    product_id,
            "user_id":       review_data.get("user_id"),
            "reviewer_name": review_data.get("reviewer_name"),
            "user_email":    review_data.get("user_email"),
            "rating":        review_data.get("rating"),
            "title":         review_data.get("title"),
            "comment":       review_data.get("comment"),
            "createdAt":     datetime.utcnow()
        }
        result = db.reviews.insert_one(review)
        review["_id"] = str(result.inserted_id)
        avg_data = list(db.reviews.aggregate([
            {"$match": {"product_id": product_id}},
            {"$group": {"_id": None, "avg": {"$avg": "$rating"}, "count": {"$sum": 1}}}
        ]))
        if avg_data:
            db.products.update_one(
                {"_id": ObjectId(product_id)},
                {"$set": {"rating": round(avg_data[0]["avg"], 2), "reviews_count": avg_data[0]["count"]}}
            )
        return {"success": True, "review": review}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/reviews/{product_id}")
def get_reviews(product_id: str):
    try:
        reviews = list(db.reviews.find({"product_id": product_id}).sort("createdAt", -1))
        for r in reviews:
            r["_id"] = str(r["_id"])
        return {"success": True, "reviews": reviews}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= AUTH =============

@app.post("/api/auth/verify-firebase-token")
def verify_firebase_token(data: dict):
    try:
        token = data.get("token")
        if not token:
            raise HTTPException(status_code=400, detail="Token required")
        try:
            decoded = fb_auth.verify_id_token(token)
            uid     = decoded["uid"]
            email   = decoded.get("email", "")
        except Exception as verify_err:
            print(f"Firebase token verification failed: {verify_err}")
            raise HTTPException(status_code=401, detail="Invalid or expired Firebase token")
        user_data = {
            "firebase_uid": uid,
            "email":        email,
            "lastLogin":    datetime.utcnow(),
        }
        db.users.update_one({"firebase_uid": uid}, {"$set": user_data}, upsert=True)
        user    = db.users.find_one({"firebase_uid": uid})
        user_id = str(user["_id"]) if user else uid
        return {"success": True, "user": {"id": user_id, "email": email, "firebase_uid": uid}}
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
            return {"success": True, "token": token, "email": admin_email}
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= ORDERS =============

@app.post("/api/orders/create")
def create_order_v2(order_req: CreateOrderRequest, authorization: str = Header(None)):
    try:
        if not authorization:
            raise HTTPException(status_code=401, detail="Unauthorized")
        items = []
        for item in order_req.items:
            item_dict = item.dict()
            try:
                product = db.products.find_one({"_id": ObjectId(item.product_id)})
                if product:
                    item_dict["price"]        = product.get("base_price", item.price or 0)
                    item_dict["product_name"] = item.product_name or product.get("name", "")
            except:
                pass
            item_dict["customisation"] = (item.customisation or "").strip() or None
            items.append(item_dict)
        shipping = order_req.shipping_details.dict() if order_req.shipping_details else {}
        order_doc = {
            "user_id":           order_req.user_id,
            "items":             items,
            "total_amount":      order_req.total_amount,
            "shipping_details":  shipping,
            "order_status":      "pending",
            "payment_status":    "pending",
            "createdAt":         datetime.utcnow(),
            "user_email":        shipping.get("email", ""),
            "user_phone":        shipping.get("phone", ""),
            "has_customisation": any(i.get("customisation") for i in items),
        }
        result   = db.orders.insert_one(order_doc)
        order_id = str(result.inserted_id)
        order_doc["_id"] = order_id
        razorpay_order = {"id": f"order_{order_id}", "amount": int(order_req.total_amount * 100), "currency": "INR"}
        try:
            import razorpay as rz
            rz_key    = os.getenv("RAZORPAY_KEY_ID")
            rz_secret = os.getenv("RAZORPAY_KEY_SECRET")
            if rz_key and rz_secret:
                rz_client      = rz.Client(auth=(rz_key, rz_secret))
                razorpay_order = rz_client.order.create({"amount": int(order_req.total_amount * 100), "currency": "INR", "receipt": order_id})
        except Exception as rz_err:
            print(f"Razorpay order creation failed: {rz_err}")
        return {"success": True, "order": {"id": order_id, **order_doc}, "razorpay_order": razorpay_order}
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
            {"$set": {"payment_status": "paid", "order_status": "confirmed", "paidAt": datetime.utcnow()}}
        )
        return {"success": True, "message": "Payment verified"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/orders/user/{user_id}")
def get_user_orders(user_id: str, authorization: str = Header(None)):
    try:
        if not authorization:
            raise HTTPException(status_code=401, detail="Unauthorized")
        orders = list(db.orders.find({"user_id": user_id}).sort("createdAt", -1))
        for o in orders:
            o["_id"] = str(o["_id"])
            o["id"]  = o["_id"]
        return orders
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= ADMIN ORDERS =============

@app.get("/api/admin/orders")
def get_all_orders(admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized")
        orders = list(db.orders.find().sort("createdAt", -1))
        for o in orders:
            o["_id"] = str(o["_id"])
            o["id"]  = o["_id"]
        return {"success": True, "count": len(orders), "orders": orders}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/admin/orders/{order_id}")
def update_order_status(order_id: str, status_data: dict, admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized")
        result = db.orders.update_one(
            {"_id": ObjectId(order_id)},
            {"$set": {"order_status": status_data.get("status"), "updatedAt": datetime.utcnow()}}
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
            raise HTTPException(status_code=401, detail="Unauthorized")
        total_products  = db.products.count_documents({})
        total_orders    = db.orders.count_documents({})
        total_customers = db.users.count_documents({})
        revenue_data = list(db.orders.aggregate([
            {"$match": {"payment_status": "paid"}},
            {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}}
        ]))
        total_revenue = revenue_data[0]["total"] if revenue_data else 0
        order_status  = list(db.orders.aggregate([{"$group": {"_id": "$order_status", "count": {"$sum": 1}}}]))
        custom_orders = db.orders.count_documents({"has_customisation": True})
        return {
            "success": True,
            "stats": {
                "total_products":   total_products,
                "total_orders":     total_orders,
                "total_customers":  total_customers,
                "total_revenue":    total_revenue,
                "custom_orders":    custom_orders,
                "order_status":     {item["_id"]: item["count"] for item in order_status}
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= ADMIN CUSTOMERS =============

@app.get("/api/admin/customers")
def get_all_customers(admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized")
        customers = list(db.users.find().sort("lastLogin", -1))
        for c in customers:
            c["_id"] = str(c["_id"])
        return {"success": True, "count": len(customers), "customers": customers}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= RUN =============

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
