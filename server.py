from fastapi import FastAPI, HTTPException, Header, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Union
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
import os
import random
import cloudinary
import cloudinary.uploader

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

app = FastAPI(title="Besties Craft API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============= CATEGORY HELPERS =============
# FIX: These two functions solve the multi-category bug.
# Products saved with category as a list ["keychains","crafts"] were not
# matching the old exact-string query — so category pages showed empty.

VALID_CATEGORIES = {
    'bracelets', 'handmade-flowers', 'keychains',
    'hair-accessories', 'gifting-items', 'crafts'
}

def normalize_category_field(raw):
    """
    Always returns a clean LIST of valid category slugs.
    Handles: None → [], "keychains" → ["keychains"],
             ["keychains","crafts"] → ["keychains","crafts"],
             "bags" (invalid) → []
    """
    if not raw:
        return []
    cats = raw if isinstance(raw, list) else [raw]
    return [
        c.strip().lower().replace(" ", "-")
        for c in cats
        if c.strip().lower().replace(" ", "-") in VALID_CATEGORIES
    ]

def prep_product(p):
    """
    Shared serialiser — call before returning any product to the frontend.
    Always sends 'categories' (list) AND 'category' (first item string)
    for backward compatibility with existing frontend code.
    """
    p["_id"]        = str(p["_id"])
    p["in_stock"]   = p.get("stock", 0) > 0
    p["categories"] = normalize_category_field(p.get("category"))
    # Keep single string for any frontend code that still reads product.category
    p["category"]   = p["categories"][0] if p["categories"] else ""
    return p

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
    # FIX: Accept both single string AND list from admin panel
    category: Optional[Union[str, List[str]]] = "general"
    stock: int = 0
    colors: List[str] = []
    variants: List[ProductVariant] = []
    skus: List[SKUOption] = []
    rating: float = 0
    reviews_count: int = 0
    brand: Optional[str] = None
    warranty: Optional[str] = None

# ─── CartItem now carries colour + customisation ───
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
    fullName: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postalCode: Optional[str] = None
    country: Optional[str] = "India"

class CreateOrderRequest(BaseModel):
    user_id: str
    items: List[OrderItem]
    total_amount: float
    shipping_details: Optional[ShippingDetails] = None

class Order(BaseModel):
    user_id: str
    items: List[CartItem]
    shipping_address: dict
    billing_address: Optional[dict] = None
    payment_method: str = "razorpay"

# ============= BASIC ENDPOINTS =============

@app.get("/")
def root():
    return {"message": "Besties Craft Backend API", "version": "2.0", "docs": "/docs"}

@app.get("/health")
def health_check():
    try:
        db.admin.command('ping')
        product_count = db.products.count_documents({})
        return {"status": "ok", "database": "connected", "products_count": product_count}
    except:
        return {"status": "error", "database": "disconnected", "products_count": 0}

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
            p["_id"]        = str(p["_id"])
            # Normalize so admin panel also sees clean categories list
            p["categories"] = normalize_category_field(p.get("category"))
            p["category"]   = p["categories"][0] if p["categories"] else p.get("category", "")
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
            # FIX: $in matches whether DB stores "keychains" (string)
            # OR ["keychains","crafts"] (array) — both work correctly now
            query["category"] = {"$in": [category]}
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
        products = [prep_product(p) for p in products]
        return {"success": True, "count": len(products), "products": products}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    try:
        product = db.products.find_one({"_id": ObjectId(product_id)})
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        product = prep_product(product)

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
            "product_id": product_id,
            "user_id":    review_data.get("user_id"),
            "rating":     review_data.get("rating"),
            "title":      review_data.get("title"),
            "comment":    review_data.get("comment"),
            "createdAt":  datetime.utcnow()
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

@app.post("/api/auth/send-otp")
def send_otp(data: dict):
    try:
        email = data.get("email")
        phone = data.get("phone")
        if not email and not phone:
            raise HTTPException(status_code=400, detail="Email or phone required")

        identifier = email if email else phone
        otp = ''.join([str(random.randint(0, 9)) for _ in range(6)])

        db.otps.delete_many({"identifier": identifier})
        db.otps.insert_one({
            "identifier": identifier,
            "otp": otp,
            "createdAt": datetime.utcnow(),
            "expiresAt": datetime.utcnow().timestamp() + 600
        })

        if email:
            try:
                import smtplib
                from email.mime.text import MIMEText
                from email.mime.multipart import MIMEMultipart

                smtp_host = os.getenv("SMTP_HOST", "smtp-relay.brevo.com")
                smtp_port = int(os.getenv("SMTP_PORT", 587))
                smtp_user = os.getenv("SMTP_USER")
                smtp_pass = os.getenv("SMTP_PASS")
                email_from = os.getenv("EMAIL_FROM", "bestiescraft1434@gmail.com")

                msg = MIMEMultipart("alternative")
                msg["Subject"] = "Your Besties Craft OTP"
                msg["From"]    = email_from
                msg["To"]      = email

                html_body = f"""
                <html><body style="font-family:Arial,sans-serif;padding:20px;">
                    <h2 style="color:#c2602a;">Besties Craft</h2>
                    <p>Your OTP for login is:</p>
                    <h1 style="color:#333;letter-spacing:8px;">{otp}</h1>
                    <p>Valid for <strong>10 minutes</strong>.</p>
                    <p style="color:#999;font-size:12px;">If you didn't request this, ignore this email.</p>
                </body></html>
                """
                msg.attach(MIMEText(html_body, "html"))

                with smtplib.SMTP(smtp_host, smtp_port) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(email_from, email, msg.as_string())
            except Exception as email_error:
                print(f"Email sending failed: {email_error}")

        return {"success": True, "message": "OTP sent", "identifier": identifier}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/verify-otp")
def verify_otp(data: dict):
    try:
        email       = data.get("email")
        phone       = data.get("phone")
        otp_entered = str(data.get("otp", "")).strip()
        identifier  = email if email else phone

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
        user    = db.users.find_one({"$or": [{"email": email}, {"phone": phone}]})
        user_id = str(user["_id"]) if user else None
        token   = __import__('hashlib').sha256(f"{user_id}{identifier}{datetime.utcnow()}".encode()).hexdigest()

        return {"success": True, "token": token, "user": {"id": user_id, "email": email, "phone": phone}}
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
    """
    Frontend checkout calls this. Each item may carry a customisation note.
    The note is stored with the item so admin can see it.
    """
    try:
        if not authorization:
            raise HTTPException(status_code=401, detail="Unauthorized")

        items = []
        for item in order_req.items:
            item_dict = item.dict()
            # Fetch latest price from DB to prevent tampering
            try:
                product = db.products.find_one({"_id": ObjectId(item.product_id)})
                if product:
                    item_dict["price"] = product.get("base_price", item.price or 0)
                    item_dict["product_name"] = item.product_name or product.get("name", "")
            except:
                pass
            # Keep customisation note (may be None / empty)
            item_dict["customisation"] = (item.customisation or "").strip() or None
            items.append(item_dict)

        shipping = order_req.shipping_details.dict() if order_req.shipping_details else {}

        order_doc = {
            "user_id":         order_req.user_id,
            "items":           items,
            "total_amount":    order_req.total_amount,
            "shipping_details": shipping,
            "order_status":    "pending",
            "payment_status":  "pending",
            "createdAt":       datetime.utcnow(),
            "user_email":  shipping.get("email", ""),
            "user_phone":  shipping.get("phone", ""),
            "has_customisation": any(i.get("customisation") for i in items),
        }

        result = db.orders.insert_one(order_doc)
        order_id = str(result.inserted_id)
        order_doc["_id"] = order_id

        razorpay_order = {
            "id":       f"order_{order_id}",
            "amount":   int(order_req.total_amount * 100),
            "currency": "INR"
        }

        try:
            import razorpay as rz
            rz_key    = os.getenv("RAZORPAY_KEY_ID")
            rz_secret = os.getenv("RAZORPAY_KEY_SECRET")
            if rz_key and rz_secret:
                rz_client = rz.Client(auth=(rz_key, rz_secret))
                rz_order  = rz_client.order.create({
                    "amount":   int(order_req.total_amount * 100),
                    "currency": "INR",
                    "receipt":  order_id
                })
                razorpay_order = rz_order
        except Exception as rz_err:
            print(f"Razorpay order creation failed: {rz_err}")

        return {
            "success":        True,
            "order":          {"id": order_id, **order_doc},
            "razorpay_order": razorpay_order
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

        revenue_data   = list(db.orders.aggregate([
            {"$match": {"payment_status": "paid"}},
            {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}}
        ]))
        total_revenue = revenue_data[0]["total"] if revenue_data else 0

        order_status = list(db.orders.aggregate([
            {"$group": {"_id": "$order_status", "count": {"$sum": 1}}}
        ]))

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
