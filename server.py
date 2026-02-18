from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
import os
import random
import string
import requests
import hashlib
import logging

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("besties-craft")

# MongoDB Connection
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "besties_craft_db")

try:
    client = MongoClient(MONGO_URI)
    db = client[DATABASE_NAME]
    print("✅ MongoDB connected successfully")
except Exception as e:
    print(f"❌ MongoDB connection failed: {e}")
    logger.error(f"MongoDB connection failed: {e}")

# Product Schema
class Product(BaseModel):
    name: str
    description: str
    price: float
    image: str
    stock: int = 10
    category: str = "general"

app = FastAPI()

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Helper function to generate OTP
def generate_otp():
    return ''.join(random.choices(string.digits, k=6))

# Helper function to send email with Brevo
def send_email(recipient_email, subject, body):
    try:
        api_key = os.getenv("BREVO_API_KEY")
        
        if not api_key:
            logger.warning("BREVO_API_KEY not found in environment variables")
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
            logger.info(f"Email sent successfully to {recipient_email}")
            return True
        else:
            logger.error(f"Email error: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Email error: {str(e)}")
        return False

# Helper function to send SMS with Twilio
def send_sms(phone_number, otp):
    try:
        from twilio.rest import Client
        
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        twilio_phone = os.getenv("TWILIO_PHONE_NUMBER")
        
        if not all([account_sid, auth_token, twilio_phone]):
            logger.warning("Twilio credentials not found in environment variables")
            return False
        
        client = Client(account_sid, auth_token)
        
        message = client.messages.create(
            body=f"Your Besties Craft OTP is: {otp}. This expires in 10 minutes.",
            from_=twilio_phone,
            to=phone_number
        )
        
        logger.info(f"SMS sent successfully to {phone_number}: {message.sid}")
        return True
        
    except Exception as e:
        logger.error(f"SMS error: {str(e)}")
        return False

# Health Check
@app.get("/health")
def health_check():
    logger.info("Health check called")
    return {"status": "ok"}

# Root endpoint
@app.get("/")
def root():
    logger.info("Root endpoint called")
    return {
        "message": "Besties Craft Backend API",
        "docs": "/docs",
        "health": "/health"
    }

# ============= PRODUCTS ENDPOINTS =============

# GET all products
@app.get("/api/products")
def get_products():
    try:
        logger.info("Fetching all products")
        products = list(db.products.find())
        for product in products:
            product["_id"] = str(product["_id"])
        logger.info(f"Returned {len(products)} products")
        return products
    except Exception as e:
        logger.error(f"Error fetching products: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# GET all products (FALLBACK without /api)
@app.get("/products")
def get_products_fallback():
    """Fallback route for requests without /api prefix"""
    return get_products()

# GET single product by ID
@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    try:
        logger.info(f"Fetching product with ID: {product_id}")
        product_id = ObjectId(product_id)
        product = db.products.find_one({"_id": product_id})
        
        if not product:
            logger.warning(f"Product not found: {product_id}")
            raise HTTPException(status_code=404, detail="Product not found")
        
        product["_id"] = str(product["_id"])
        logger.info(f"Product fetched successfully: {product_id}")
        return product
    except Exception as e:
        logger.error(f"Error fetching product: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# GET single product by ID (FALLBACK without /api)
@app.get("/products/{product_id}")
def get_product_fallback(product_id: str):
    """Fallback route for requests without /api prefix"""
    return get_product(product_id)

# CREATE product (Admin only)
@app.post("/api/products")
def create_product(product: Product, admin_token: str = Header(None)):
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            logger.warning("Unauthorized product creation attempt")
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        logger.info(f"Creating new product: {product.name}")
        product_dict = product.dict()
        product_dict["createdAt"] = datetime.utcnow()
        product_dict["updatedAt"] = datetime.utcnow()
        product_dict["stock"] = max(product_dict.get("stock", 10), 0)
        product_dict["inStock"] = product_dict["stock"] > 0
        
        result = db.products.insert_one(product_dict)
        product_dict["_id"] = str(result.inserted_id)
        
        logger.info(f"Product created successfully: {result.inserted_id}")
        return {"message": "Product created", "product": product_dict}
    except Exception as e:
        logger.error(f"Error creating product: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# UPDATE product (Admin only)
@app.put("/api/products/{product_id}")
def update_product(product_id: str, product: Product, admin_token: str = Header(None)):
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            logger.warning("Unauthorized product update attempt")
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        logger.info(f"Updating product: {product_id}")
        product_id = ObjectId(product_id)
        product_dict = product.dict()
        product_dict["updatedAt"] = datetime.utcnow()
        product_dict["stock"] = max(product_dict.get("stock", 10), 0)
        product_dict["inStock"] = product_dict["stock"] > 0
        
        result = db.products.update_one(
            {"_id": product_id},
            {"$set": product_dict}
        )
        
        if result.matched_count == 0:
            logger.warning(f"Product not found for update: {product_id}")
            raise HTTPException(status_code=404, detail="Product not found")
        
        logger.info(f"Product updated successfully: {product_id}")
        return {"message": "Product updated", "modified_count": result.modified_count}
    except Exception as e:
        logger.error(f"Error updating product: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# DELETE product (Admin only)
@app.delete("/api/products/{product_id}")
def delete_product(product_id: str, admin_token: str = Header(None)):
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            logger.warning("Unauthorized product deletion attempt")
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        logger.info(f"Deleting product: {product_id}")
        product_id = ObjectId(product_id)
        result = db.products.delete_one({"_id": product_id})
        
        if result.deleted_count == 0:
            logger.warning(f"Product not found for deletion: {product_id}")
            raise HTTPException(status_code=404, detail="Product not found")
        
        logger.info(f"Product deleted successfully: {product_id}")
        return {"message": "Product deleted"}
    except Exception as e:
        logger.error(f"Error deleting product: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= AUTH ENDPOINTS =============

# SEND OTP
@app.post("/api/auth/send-otp")
def send_otp(data: dict):
    try:
        email = data.get("email")
        phone = data.get("phone")
        
        if not email and not phone:
            logger.warning("Send OTP: Missing email or phone")
            raise HTTPException(status_code=400, detail="Missing email or phone")
        
        identifier = email if email else phone
        login_method = "email" if email else "phone"
        
        logger.info(f"Sending OTP to {login_method}: {identifier}")
        
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
        logger.info(f"OTP stored in database for {identifier}")
        
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
                logger.error("Failed to send email OTP")
                raise HTTPException(status_code=500, detail="Failed to send email")
        
        elif login_method == "phone":
            sms_sent = send_sms(phone, otp)
            
            if not sms_sent:
                logger.error("Failed to send SMS OTP")
                raise HTTPException(status_code=500, detail="Failed to send SMS")
        
        return {
            "success": True,
            "message": f"OTP sent to your {login_method}",
            "identifier": identifier
        }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Send OTP Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# VERIFY OTP
@app.post("/api/auth/verify-otp")
def verify_otp(data: dict):
    try:
        email = data.get("email")
        phone = data.get("phone")
        otp_entered = str(data.get("otp", "")).strip()
        
        identifier = email if email else phone
        login_method = "email" if email else "phone"
        
        if not identifier or not otp_entered:
            logger.warning("Verify OTP: Missing email/phone or OTP")
            raise HTTPException(status_code=400, detail="Missing email/phone or OTP")
        
        logger.info(f"Verifying OTP for {login_method}: {identifier}")
        
        otp_record = db.otps.find_one({"identifier": identifier})
        
        if not otp_record:
            logger.warning(f"OTP not found for {identifier}")
            raise HTTPException(status_code=401, detail="OTP not found or expired")
        
        if datetime.utcnow().timestamp() > otp_record.get("expiresAt", 0):
            db.otps.delete_one({"_id": otp_record["_id"]})
            logger.warning(f"OTP expired for {identifier}")
            raise HTTPException(status_code=401, detail="OTP has expired")
        
        if str(otp_record["otp"]) != otp_entered:
            logger.warning(f"Invalid OTP for {identifier}")
            raise HTTPException(status_code=401, detail="Invalid OTP")
        
        logger.info(f"OTP verified successfully for {identifier}")
        
        db.otps.delete_one({"_id": otp_record["_id"]})
        
        user_data = {
            login_method: identifier,
            "lastLogin": datetime.utcnow()
        }
        
        result = db.users.update_one(
            {login_method: identifier},
            {"$set": user_data},
            upsert=True
        )
        
        user
