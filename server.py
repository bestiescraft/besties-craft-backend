from fastapi import FastAPI, HTTPException, Header, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, validator
from typing import List, Optional, Union
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
import os
import hmac
import hashlib
import httpx
import cloudinary
import cloudinary.uploader
import firebase_admin
from firebase_admin import auth as fb_auth

# ============= SETUP =============

MONGO_URI     = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "besties_craft_db")

client = MongoClient(MONGO_URI)
db     = client[DATABASE_NAME]

cloudinary.config(
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key    = os.getenv("CLOUDINARY_API_KEY"),
    api_secret = os.getenv("CLOUDINARY_API_SECRET")
)

_FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")
if _FIREBASE_PROJECT_ID and not firebase_admin._apps:
    try:
        firebase_admin.initialize_app(options={"projectId": _FIREBASE_PROJECT_ID})
    except Exception as _fe:
        print(f"Firebase Admin init warning: {_fe}")

app = FastAPI(title="Besties Craft API", version="2.0")

ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "https://www.bestiescraft.in,https://bestiescraft.in,https://besties-craft-frontend.vercel.app"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============= SHIPROCKET CONFIG =============

SHIPROCKET_EMAIL    = os.getenv("SHIPROCKET_EMAIL", "")
SHIPROCKET_PASSWORD = os.getenv("SHIPROCKET_PASSWORD", "")
SHIPROCKET_API      = "https://apiv2.shiprocket.in/v1/external"

PICKUP_PINCODE = os.getenv("PICKUP_PINCODE", "221007")
DEFAULT_WEIGHT = 0.5

_sr_token_cache = {"token": None, "fetched_at": None}


async def get_shiprocket_token() -> str:
    import time
    now = time.time()
    if _sr_token_cache["token"] and _sr_token_cache["fetched_at"]:
        if now - _sr_token_cache["fetched_at"] < 23 * 3600:
            return _sr_token_cache["token"]

    if not SHIPROCKET_EMAIL or not SHIPROCKET_PASSWORD:
        raise HTTPException(
            status_code=500,
            detail="Shiprocket credentials not configured. Add SHIPROCKET_EMAIL and SHIPROCKET_PASSWORD in Render → Environment."
        )

    try:
        async with httpx.AsyncClient() as c:
            resp = await c.post(
                f"{SHIPROCKET_API}/auth/login",
                json={"email": SHIPROCKET_EMAIL, "password": SHIPROCKET_PASSWORD},
                timeout=15
            )
    except httpx.TimeoutException:
        _sr_token_cache["token"] = None
        _sr_token_cache["fetched_at"] = None
        raise HTTPException(status_code=500, detail="Shiprocket login timed out. Please try again.")
    except Exception as e:
        _sr_token_cache["token"] = None
        _sr_token_cache["fetched_at"] = None
        raise HTTPException(status_code=500, detail=f"Shiprocket network error: {e}")

    if resp.status_code != 200:
        _sr_token_cache["token"] = None
        _sr_token_cache["fetched_at"] = None
        raise HTTPException(
            status_code=500,
            detail=f"Shiprocket login failed (HTTP {resp.status_code}). Check credentials in Render env vars."
        )

    token = resp.json().get("token")
    if not token:
        _sr_token_cache["token"] = None
        _sr_token_cache["fetched_at"] = None
        raise HTTPException(status_code=500, detail="Shiprocket returned no token.")

    _sr_token_cache["token"]      = token
    _sr_token_cache["fetched_at"] = now
    return token


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
    p["category"]   = p["categories"][0] if p["categories"] else "general"
    p["in_stock"]   = p.get("stock", 0) > 0
    return p


def fix_order_out(o: dict) -> dict:
    o["_id"] = str(o["_id"])
    o["id"]  = o["_id"]
    raw_dt = o.get("createdAt") or o.get("created_at")
    if isinstance(raw_dt, datetime):
        o["created_at"] = raw_dt.isoformat()
    elif isinstance(raw_dt, str):
        o["created_at"] = raw_dt
    else:
        o["created_at"] = datetime.utcnow().isoformat()
    return o


def get_razorpay_client():
    try:
        import razorpay as rz
        rz_key    = os.getenv("RAZORPAY_KEY_ID", "")
        rz_secret = os.getenv("RAZORPAY_KEY_SECRET", "")
        if rz_key and rz_secret:
            return rz.Client(auth=(rz_key, rz_secret)), rz_key, rz_secret
    except Exception as e:
        print(f"Razorpay init error: {e}")
    return None, None, None


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
    weight_grams: Optional[int] = 500

    @validator("categories", pre=True, always=True)
    def normalise_cats(cls, v, values):
        raw = v if v is not None else values.get("category")
        return normalise_categories(raw)


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


# ============= BASIC ENDPOINTS =============

@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"message": "Besties Craft Backend API", "version": "2.0", "docs": "/docs"}


@app.api_route("/health", methods=["GET", "HEAD"])
def health_check():
    try:
        db.admin.command('ping')
        return {"status": "ok", "database": "connected", "products_count": db.products.count_documents({})}
    except Exception:
        return {"status": "error", "database": "disconnected", "products_count": 0}


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    return """User-agent: *
Allow: /

Sitemap: https://www.bestiescraft.in/sitemap.xml
"""


@app.get("/sitemap.xml")
def sitemap_xml():
    base = "https://www.bestiescraft.in"
    now  = datetime.utcnow().strftime("%Y-%m-%d")

    static_urls = [
        ("", "1.0", "daily"),
        ("/products", "0.9", "daily"),
        ("/about", "0.7", "monthly"),
        ("/contact", "0.7", "monthly"),
        ("/track-order", "0.5", "monthly"),
    ]

    urls_xml = ""
    for path, priority, changefreq in static_urls:
        urls_xml += f"""  <url>
    <loc>{base}{path}</loc>
    <lastmod>{now}</lastmod>
    <changefreq>{changefreq}</changefreq>
    <priority>{priority}</priority>
  </url>
"""

    try:
        products = list(db.products.find({}, {"_id": 1, "updatedAt": 1}))
        for p in products:
            pid      = str(p["_id"])
            last_mod = p.get("updatedAt", datetime.utcnow())
            if isinstance(last_mod, datetime):
                last_mod = last_mod.strftime("%Y-%m-%d")
            else:
                last_mod = now
            urls_xml += f"""  <url>
    <loc>{base}/products/{pid}</loc>
    <lastmod>{last_mod}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
"""
    except Exception:
        pass

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls_xml}</urlset>"""

    return Response(content=xml, media_type="application/xml")


@app.get("/api/debug/shiprocket")
async def debug_shiprocket():
    email    = os.getenv("SHIPROCKET_EMAIL", "NOT SET")
    password = os.getenv("SHIPROCKET_PASSWORD", "NOT SET")
    masked   = password[:3] + "***" + password[-2:] if len(password) > 5 else "TOO SHORT"
    try:
        async with httpx.AsyncClient() as c:
            resp = await c.post(
                "https://apiv2.shiprocket.in/v1/external/auth/login",
                json={"email": email, "password": password},
                timeout=15
            )
        return {
            "email":           email,
            "password_masked": masked,
            "status_code":     resp.status_code,
            "response":        resp.json() if resp.status_code in (200, 401, 403) else resp.text[:300],
            "success":         resp.status_code == 200
        }
    except Exception as e:
        return {"email": email, "password_masked": masked, "error": str(e)}


# ============= MIGRATION =============

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
            query["$or"] = [{"categories": {"$in": [category]}}, {"category": category}]
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
        product_dict["createdAt"]  = datetime.utcnow()
        product_dict["updatedAt"]  = datetime.utcnow()
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
        product_dict["updatedAt"]  = datetime.utcnow()
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

        # ✅ FIX: Save name and phone so admin customers page shows real names
        user_data = {
            "firebase_uid": uid,
            "email":        email,
            "name":         decoded.get("name") or decoded.get("display_name") or email.split("@")[0],
            "phone":        decoded.get("phone_number") or "",
            "lastLogin":    datetime.utcnow()
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
        admin_password = os.getenv("ADMIN_PASSWORD")
        if not admin_password:
            raise HTTPException(status_code=500, detail="Admin password not configured on server.")
        if password == admin_password:
            admin_email = os.getenv("ADMIN_EMAIL", "bestiescraft1434@gmail.com")
            token = hashlib.sha256(f"{admin_email}{datetime.utcnow()}".encode()).hexdigest()
            return {"success": True, "token": token, "email": admin_email}
        raise HTTPException(status_code=401, detail="Invalid credentials")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============= SHIPPING RATES =============

class CartItem(BaseModel):
    product_id: str
    quantity:   int = 1


class ShippingRateRequest(BaseModel):
    delivery_pincode: str
    cart_items:       Optional[List[CartItem]] = None
    weight:           Optional[float]          = None


@app.post("/api/shipping-rates")
async def get_shipping_rates(req: ShippingRateRequest):
    try:
        delivery_pincode = req.delivery_pincode.strip()

        if not delivery_pincode or not delivery_pincode.isdigit() or len(delivery_pincode) != 6:
            raise HTTPException(status_code=400, detail="Invalid pincode — must be 6 digits")

        weight_kg = DEFAULT_WEIGHT
        if req.cart_items:
            total_grams = 0
            for item in req.cart_items:
                try:
                    product = db.products.find_one({"_id": ObjectId(item.product_id)})
                    grams   = int(product.get("weight_grams", 500)) if product else 500
                    total_grams += grams * item.quantity
                except Exception:
                    total_grams += 500 * item.quantity
            weight_kg = max(round(total_grams / 1000, 2), 0.1)
        elif req.weight:
            weight_kg = req.weight

        token = await get_shiprocket_token()

        params = {
            "pickup_postcode":   PICKUP_PINCODE,
            "delivery_postcode": delivery_pincode,
            "weight":            weight_kg,
            "cod":               0,
        }

        async with httpx.AsyncClient() as c:
            resp = await c.get(
                f"{SHIPROCKET_API}/courier/serviceability/",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )

        if resp.status_code == 401:
            _sr_token_cache["token"] = None
            _sr_token_cache["fetched_at"] = None
            token = await get_shiprocket_token()
            async with httpx.AsyncClient() as c:
                resp = await c.get(
                    f"{SHIPROCKET_API}/courier/serviceability/",
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=15,
                )

        if resp.status_code != 200:
            return {
                "success": False, "shipping_cost": 60,
                "weight_kg": weight_kg, "courier": None,
                "message": "Could not fetch live rates. Flat ₹60 applied.",
            }

        data     = resp.json()
        couriers = data.get("data", {}).get("available_courier_companies", [])

        if not couriers:
            return {
                "success": False, "shipping_cost": 60,
                "weight_kg": weight_kg, "courier": None,
                "message": "No courier available for this pincode. Flat ₹60 applied.",
            }

        cheapest      = min(couriers, key=lambda x: float(x.get("rate", 9999)))
        shipping_cost = float(cheapest.get("rate", 60))
        courier_name  = cheapest.get("courier_name", "")
        etd           = cheapest.get("etd", "")

        return {
            "success": True,
            "shipping_cost": round(shipping_cost, 2),
            "weight_kg": weight_kg,
            "courier": courier_name,
            "etd": etd,
            "message": f"Shipping via {courier_name} ({weight_kg}kg)",
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Shipping rate error: {e}")
        return {
            "success": False, "shipping_cost": 60,
            "weight_kg": DEFAULT_WEIGHT, "courier": None,
            "message": "Could not fetch live rates. Flat ₹60 applied.",
        }


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
            except Exception:
                pass
            item_dict["customisation"] = (item.customisation or "").strip() or None
            items.append(item_dict)

        shipping     = order_req.shipping_details.dict() if order_req.shipping_details else {}
        amount_paise = int(order_req.total_amount * 100)

        rz_client, _, _ = get_razorpay_client()
        if rz_client:
            try:
                razorpay_order = rz_client.order.create({
                    "amount":   amount_paise,
                    "currency": "INR",
                    "notes":    {"user_id": order_req.user_id, "email": shipping.get("email", "")}
                })
            except Exception as rz_err:
                raise HTTPException(status_code=500, detail=f"Payment gateway error: {rz_err}")
        else:
            razorpay_order = {
                "id":       f"order_dev_{int(datetime.utcnow().timestamp())}",
                "amount":   amount_paise,
                "currency": "INR"
            }

        db.pending_payments.insert_one({
            "razorpay_order_id": razorpay_order["id"],
            "user_id":           order_req.user_id,
            "items":             items,
            "total_amount":      order_req.total_amount,
            "shipping":          shipping,
            "created_at":        datetime.utcnow(),
        })

        return {"success": True, "razorpay_order": razorpay_order, "order": {"id": razorpay_order["id"]}}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/orders/verify-payment")
def verify_payment(payment_data: dict):
    try:
        razorpay_order_id   = payment_data.get("razorpay_order_id")
        razorpay_payment_id = payment_data.get("razorpay_payment_id")
        razorpay_signature  = payment_data.get("razorpay_signature")

        if not razorpay_order_id or not razorpay_payment_id:
            raise HTTPException(status_code=400, detail="Missing payment details")

        _, _, rz_secret = get_razorpay_client()

        if rz_secret and razorpay_signature:
            msg_str            = f"{razorpay_order_id}|{razorpay_payment_id}"
            expected_signature = hmac.new(
                rz_secret.encode("utf-8"),
                msg_str.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected_signature, str(razorpay_signature)):
                raise HTTPException(status_code=400, detail="Payment signature verification failed")

        pending = db.pending_payments.find_one({"razorpay_order_id": razorpay_order_id})
        if not pending:
            raise HTTPException(status_code=404, detail="Pending payment record not found")

        now = datetime.utcnow()
        order_doc = {
            "user_id":                pending["user_id"],
            "items":                  pending["items"],
            "total_amount":           pending["total_amount"],
            "shipping_details":       pending["shipping"],
            "order_status":           "confirmed",
            "payment_status":         "paid",
            "createdAt":              now,
            "created_at":             now.isoformat(),
            "paidAt":                 now,
            "razorpay_order_id":      razorpay_order_id,
            "razorpay_payment_id":    razorpay_payment_id,
            "user_email":             pending["shipping"].get("email", ""),
            "user_phone":             pending["shipping"].get("phone", ""),
            "has_customisation":      any(i.get("customisation") for i in pending["items"]),
            "shiprocket_order_id":    None,
            "shiprocket_shipment_id": None,
            "shiprocket_awb":         None,
            "shiprocket_courier":     None,
            "tracking_url":           None,
        }
        result   = db.orders.insert_one(order_doc)
        order_id = str(result.inserted_id)
        db.pending_payments.delete_one({"razorpay_order_id": razorpay_order_id})
        print(f"✅ Order confirmed: {order_id} | Payment: {razorpay_payment_id}")
        return {"success": True, "message": "Payment verified!", "order_id": order_id}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/orders/cancel-pending")
def cancel_pending(data: dict):
    try:
        razorpay_order_id = data.get("razorpay_order_id")
        if razorpay_order_id:
            db.pending_payments.delete_one({"razorpay_order_id": razorpay_order_id})
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/orders/user/{user_id}")
def get_user_orders(user_id: str, authorization: str = Header(None)):
    try:
        if not authorization:
            raise HTTPException(status_code=401, detail="Unauthorized")
        orders = list(db.orders.find({"user_id": user_id, "payment_status": "paid"}).sort("createdAt", -1))
        for o in orders:
            fix_order_out(o)
        return orders
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/orders/track/{order_id}")
def track_order(order_id: str):
    try:
        order = None

        if len(order_id) == 24:
            try:
                order = db.orders.find_one({
                    "_id": ObjectId(order_id),
                    "payment_status": "paid"
                })
            except Exception:
                pass

        if not order:
            order = db.orders.find_one({
                "razorpay_order_id": order_id,
                "payment_status": "paid"
            })

        if not order:
            raise HTTPException(status_code=404, detail="Order not found. Please check your Order ID.")

        ship = order.get("shipping_details", {})

        status_map = {
            "confirmed":  {"label": "Order Confirmed",  "step": 1},
            "processing": {"label": "Being Prepared",   "step": 2},
            "shipped":    {"label": "Shipped",          "step": 3},
            "delivered":  {"label": "Delivered",        "step": 4},
            "cancelled":  {"label": "Cancelled",        "step": 0},
        }
        raw_status  = order.get("order_status", "confirmed")
        status_info = status_map.get(raw_status, {"label": raw_status.title(), "step": 1})

        created_at = order.get("createdAt") or order.get("created_at")
        if isinstance(created_at, datetime):
            created_at = created_at.isoformat()

        return {
            "success":      True,
            "order_id":     str(order["_id"]),
            "order_status": raw_status,
            "status_label": status_info["label"],
            "status_step":  status_info["step"],
            "created_at":   created_at,
            "total_amount": order.get("total_amount", 0),
            "items":        [
                {
                    "product_name":  i.get("product_name", "Product"),
                    "quantity":      i.get("quantity", 1),
                    "price":         i.get("price", 0),
                    "color":         i.get("color"),
                    "customisation": i.get("customisation"),
                }
                for i in order.get("items", [])
            ],
            "shipping": {
                "fullName":   ship.get("fullName"),
                "city":       ship.get("city"),
                "state":      ship.get("state"),
                "postalCode": ship.get("postalCode"),
                "phone":      ship.get("phone"),
            },
            "tracking": {
                "awb":          order.get("shiprocket_awb"),
                "courier":      order.get("shiprocket_courier"),
                "tracking_url": order.get("tracking_url"),
                "etd":          order.get("etd"),
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============= SHIPROCKET BOOK COURIER =============

@app.post("/api/admin/orders/{order_id}/book-courier")
async def book_courier(order_id: str, admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized")

        order = db.orders.find_one({"_id": ObjectId(order_id)})
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.get("shiprocket_awb"):
            return {
                "success":      True,
                "message":      "Shipment already booked",
                "awb":          order["shiprocket_awb"],
                "courier":      order.get("shiprocket_courier"),
                "tracking_url": order.get("tracking_url")
            }

        ship     = order.get("shipping_details", {})
        items    = order.get("items", [])
        order_no = str(order["_id"])[-8:].upper()

        token   = await get_shiprocket_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        sr_items = []
        for item in items:
            sr_items.append({
                "name":          item.get("product_name", "Handmade Product"),
                "sku":           str(item.get("product_id", "SKU001"))[:20],
                "units":         item.get("quantity", 1),
                "selling_price": str(item.get("price", 0)),
                "discount":      "",
                "tax":           "",
                "hsn":           ""
            })

        sr_payload = {
            "order_id":              f"BC-{order_no}",
            "order_date":            datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
            "pickup_location":       "Primary",
            "billing_customer_name": ship.get("fullName", "Customer"),
            "billing_last_name":     "",
            "billing_address":       ship.get("address", ""),
            "billing_address_2":     "",
            "billing_city":          ship.get("city", ""),
            "billing_pincode":       ship.get("postalCode", ""),
            "billing_state":         ship.get("state", ""),
            "billing_country":       "India",
            "billing_email":         ship.get("email", ""),
            "billing_phone":         ship.get("phone", ""),
            "shipping_is_billing":   True,
            "order_items":           sr_items,
            "payment_method":        "Prepaid",
            "shipping_charges":      0,
            "giftwrap_charges":      0,
            "transaction_charges":   0,
            "total_discount":        0,
            "sub_total":             order.get("total_amount", 0),
            "length":                15,
            "breadth":               12,
            "height":                8,
            "weight":                DEFAULT_WEIGHT,
        }

        async with httpx.AsyncClient() as c:
            create_resp = await c.post(
                f"{SHIPROCKET_API}/orders/create/adhoc",
                json=sr_payload, headers=headers, timeout=20
            )

        if create_resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=500,
                detail=f"Shiprocket order creation failed: {create_resp.text}"
            )

        sr_data        = create_resp.json()
        sr_order_id    = sr_data.get("order_id")
        sr_shipment_id = sr_data.get("shipment_id")

        awb_code = courier_name = tracking_url = None
        if sr_shipment_id:
            async with httpx.AsyncClient() as c:
                awb_resp = await c.post(
                    f"{SHIPROCKET_API}/courier/assign/awb",
                    json={"shipment_id": str(sr_shipment_id)},
                    headers=headers, timeout=20
                )
            if awb_resp.status_code == 200:
                awb_data     = awb_resp.json()
                awb_code     = awb_data.get("response", {}).get("data", {}).get("awb_code")
                courier_name = awb_data.get("response", {}).get("data", {}).get("courier_name")
                if awb_code:
                    tracking_url = f"https://shiprocket.co/tracking/{awb_code}"

        db.orders.update_one(
            {"_id": ObjectId(order_id)},
            {"$set": {
                "order_status":           "processing",
                "shiprocket_order_id":    sr_order_id,
                "shiprocket_shipment_id": sr_shipment_id,
                "shiprocket_awb":         awb_code,
                "shiprocket_courier":     courier_name,
                "tracking_url":           tracking_url,
                "updatedAt":              datetime.utcnow()
            }}
        )

        return {
            "success":      True,
            "message":      f"Shipment booked via {courier_name or 'courier'}!",
            "awb":          awb_code,
            "courier":      courier_name,
            "tracking_url": tracking_url,
            "sr_order_id":  sr_order_id
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Book courier error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/orders/{order_id}/tracking")
async def get_tracking(order_id: str, admin_token: str = Header(None)):
    try:
        if not admin_token:
            raise HTTPException(status_code=401, detail="Unauthorized")
        order = db.orders.find_one({"_id": ObjectId(order_id)})
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        awb = order.get("shiprocket_awb")
        if not awb:
            return {"success": False, "message": "No shipment booked yet"}
        token = await get_shiprocket_token()
        async with httpx.AsyncClient() as c:
            resp = await c.get(
                f"{SHIPROCKET_API}/courier/track/awb/{awb}",
                headers={"Authorization": f"Bearer {token}"}, timeout=15
            )
        return {
            "success":       True,
            "awb":           awb,
            "courier":       order.get("shiprocket_courier"),
            "tracking_url":  order.get("tracking_url"),
            "tracking_data": resp.json() if resp.status_code == 200 else {}
        }
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
        orders = list(db.orders.find({"payment_status": "paid"}).sort("createdAt", -1))
        for o in orders:
            fix_order_out(o)
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
        new_status = status_data.get("order_status") or status_data.get("status")
        result = db.orders.update_one(
            {"_id": ObjectId(order_id)},
            {"$set": {"order_status": new_status, "updatedAt": datetime.utcnow()}}
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
        total_orders    = db.orders.count_documents({"payment_status": "paid"})
        total_customers = db.users.count_documents({})
        revenue_data    = list(db.orders.aggregate([
            {"$match": {"payment_status": "paid"}},
            {"$group": {"_id": None, "total": {"$sum": "$total_amount"}}}
        ]))
        total_revenue = revenue_data[0]["total"] if revenue_data else 0
        order_status  = list(db.orders.aggregate([
            {"$match": {"payment_status": "paid"}},
            {"$group": {"_id": "$order_status", "count": {"$sum": 1}}}
        ]))
        custom_orders = db.orders.count_documents({"has_customisation": True, "payment_status": "paid"})
        return {
            "success": True,
            "stats": {
                "total_products":  total_products,
                "total_orders":    total_orders,
                "total_customers": total_customers,
                "total_revenue":   total_revenue,
                "custom_orders":   custom_orders,
                "order_status":    {item["_id"]: item["count"] for item in order_status}
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
