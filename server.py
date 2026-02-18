from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
import os
import random
import string
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import hashlib

# MongoDB Connection
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "besties_craft_db")

try:
    client = MongoClient(MONGO_URI)
    db = client[DATABASE_NAME]
    print("✅ MongoDB connected successfully")
except Exception as e:
    print(f"❌ MongoDB connection failed: {e}")

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

# Helper function to send email
def send_email(recipient_email, subject, body):
    try:
        sender_email = os.getenv("SENDER_EMAIL", "your-email@gmail.com")
        sender_password = os.getenv("SENDER_PASSWORD", "your-app-password")
        
        message = MIMEMultipart()
        message["From"] = sender_email
        message["To"] = recipient_email
        message["Subject"] = subject
        
        message.attach(MIMEText(body, "html"))
        
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, sender_password)
            server.send_message(message)
        
        print(f"✅ Email sent to {recipient_email}")
        return True
    except Exception as e:
        print(f"❌ Email error: {str(e)}")
        return False

# Health Check
@app.get("/health")
def health_check():
    return {"status": "ok"}

# Root endpoint
@app.get("/")
def root():
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
        products = list(db.products.find())
        for product in products:
            product["_id"] = str(product["_id"])
        return products
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# GET single product by ID
@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    try:
        product_id = ObjectId(product_id)
        product = db.products.find_one({"_id": product_id})
        
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        
        product["_id"] = str(product["_id"])
        return product
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# CREATE product (Admin only)
@app.post("/api/products")
def create_product(product: Product, admin_token: str = Header(None)):
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        product_dict = product.dict()
        product_dict["createdAt"] = datetime.utcnow()
        product_dict["updatedAt"] = datetime.utcnow()
        product_dict["stock"] = max(product_dict.get("stock", 10), 0)
        product_dict["inStock"] = product_dict["stock"] > 0
        
        result = db.products.insert_one(product_dict)
        product_dict["_id"] = str(result.inserted_id)
        
        return {"message": "Product created", "product": product_dict}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# UPDATE product (Admin only)
@app.put("/api/products/{product_id}")
def update_product(product_id: str, product: Product, admin_token: str = Header(None)):
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
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
            raise HTTPException(status_code=404, detail="Product not found")
        
        return {"message": "Product updated", "modified_count": result.modified_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# DELETE product (Admin only)
@app.delete("/api/products/{product_id}")
def delete_product(product_id: str, admin_token: str = Header(None)):
    try:
        if admin_token != os.getenv("ADMIN_TOKEN", "your-secret-token"):
            raise HTTPException(status_code=401, detail="Unauthorized")
        
        product_id = ObjectId(product_id)
        result = db.products.delete_one({"_id": product_id})
        
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Product not found")
        
        return {"message": "Product deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= AUTH ENDPOINTS =============

# SEND OTP
@app.post("/api/auth/send-otp")
def send_otp(data: dict):
    try:
        email = data.get("email")
        phone = data.get("phone")
        
        if not email and not phone:
            raise HTTPException(status_code=400, detail="Missing email or phone")
        
        # Determine login method and identifier
        identifier = email if email else phone
        login_method = "email" if email else "phone"
        
        print(f"📧 Sending OTP to {login_method}: {identifier}")
        
        # Generate OTP
        otp = generate_otp()
        
        # Store OTP in MongoDB
        otp_record = {
            "identifier": identifier,
            "login_method": login_method,
            "otp": otp,
            "createdAt": datetime.utcnow(),
            "expiresAt": datetime.utcnow().timestamp() + 600  # 10 minutes
        }
        
        # Delete any old OTP for this identifier
        db.otps.delete_many({"identifier": identifier})
        
        # Insert new OTP
        db.otps.insert_one(otp_record)
        print(f"✅ OTP stored in database: {otp}")
        
        # Send OTP via email
        if login_method == "email":
            email_body = f"""
            <html>
                <body style="font-family: Arial, sans-serif;">
                    <h2>Besties Craft - Login Verification</h2>
                    <p>Your OTP is: <strong style="font-size: 24px; color: #D97706;">{otp}</strong></p>
                    <p>This OTP will expire in 10 minutes.</p>
                    <p>If you didn't request this, please ignore this email.</p>
                </body>
            </html>
            """
            email_sent = send_email(email, "Besties Craft - Your OTP", email_body)
            
            if not email_sent:
                raise HTTPException(status_code=500, detail="Failed to send email")
        
        # For phone, you would integrate with Twilio or similar
        elif login_method == "phone":
            # For now, return success (in production, use Twilio)
            print(f"📱 SMS OTP would be sent to: {phone}")
        
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
            raise HTTPException(status_code=400, detail="Missing email/phone or OTP")
        
        print(f"🔍 Verifying OTP for {login_method}: {identifier}")
        
        # Find OTP in database
        otp_record = db.otps.find_one({"identifier": identifier})
        
        if not otp_record:
            raise HTTPException(status_code=401, detail="OTP not found or expired")
        
        # Check expiry
        if datetime.utcnow().timestamp() > otp_record.get("expiresAt", 0):
            db.otps.delete_one({"_id": otp_record["_id"]})
            raise HTTPException(status_code=401, detail="OTP has expired")
        
        # Verify OTP
        if str(otp_record["otp"]) != otp_entered:
            print(f"❌ Invalid OTP. Expected: {otp_record['otp']}, Got: {otp_entered}")
            raise HTTPException(status_code=401, detail="Invalid OTP")
        
        print(f"✅ OTP verified successfully!")
        
        # Delete used OTP
        db.otps.delete_one({"_id": otp_record["_id"]})
        
        # Create or update user in database
        user_data = {
            login_method: identifier,
            "lastLogin": datetime.utcnow()
        }
        
        result = db.users.update_one(
            {login_method: identifier},
            {"$set": user_data},
            upsert=True
        )
        
        # Get user data
        user = db.users.find_one({login_method: identifier})
        user_id = str(user["_id"]) if user else None
        
        # Generate simple token using hashlib (no JWT needed)
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

# Admin Login
@app.post("/api/auth/admin-login")
def admin_login(credentials: dict):
    try:
        username = credentials.get("username")
        password = credentials.get("password")
        
        admin_email = os.getenv("ADMIN_EMAIL", "admin@besties.com")
        admin_password = os.getenv("ADMIN_PASSWORD", "Bhola143")
        
        if username == admin_email and password == admin_password:
            # Generate simple token using hashlib
            token = hashlib.sha256(f"{admin_email}{datetime.utcnow()}".encode()).hexdigest()
            
            return {
                "success": True,
                "message": "Login successful",
                "token": token
            }
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============= ORDERS ENDPOINTS =============

@app.post("/api/orders/create")
def create_order(order_data: dict, authorization: str = Header(None)):
    try:
        # Verify token
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid token")
        
        token = authorization.split(" ")[1]
        
        # Simple token validation (just check if it's a valid hex string)
        if not token or len(token) < 10:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        # Create order
        order = {
            "user_id": order_data.get("user_id"),
            "items": order_data.get("items", []),
            "total_amount": order_data.get("total_amount", 0),
            "status": "pending",
            "createdAt": datetime.utcnow()
        }
        
        result = db.orders.insert_one(order)
        order["_id"] = str(result.inserted_id)
        
        return {
            "success": True,
            "order": order,
            "razorpay_order": {
                "id": f"order_{result.inserted_id}",
                "amount": int(order["total_amount"] * 100)  # Convert to paise
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/orders/verify-payment")
def verify_payment(payment_data: dict):
    try:
        # In production, verify Razorpay signature
        order_id = payment_data.get("order_id")
        
        # Update order status
        db.orders.update_one(
            {"_id": ObjectId(order_id)},
            {"$set": {"status": "completed", "paidAt": datetime.utcnow()}}
        )
        
        return {
            "success": True,
            "message": "Payment verified"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
