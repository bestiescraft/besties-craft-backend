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

# MongoDB Connection
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
client = MongoClient(MONGO_URI)
db = client["besties_craft_db"]

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
        
        return True
    except Exception as e:
        print(f"Email error: {str(e)}")
        return False

# Health Check
@app.get("/health")
def health_check():
    return {"status": "ok"}

# GET all products
@app.get("/products")
def get_products():
    try:
        products = list(db.products.find())
        for product in products:
            product["_id"] = str(product["_id"])
        return products
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# GET single product by ID
@app.get("/products/{product_id}")
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
@app.post("/products")
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
@app.put("/products/{product_id}")
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
@app.delete("/products/{product_id}")
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

# SEND OTP
@app.post("/auth/send-otp")
def send_otp(data: dict):
    try:
        login_method = data.get("loginMethod")
        identifier = data.get(login_method)  # email or phone
        
        if not identifier:
            raise HTTPException(status_code=400, detail=f"Missing {login_method}")
        
        # Generate OTP
        otp = generate_otp()
        
        # Store OTP in MongoDB (temporary collection)
        otp_record = {
            "identifier": identifier,
            "loginMethod": login_method,
            "otp": otp,
            "createdAt": datetime.utcnow(),
            "expiresAt": datetime.utcnow().timestamp() + 600  # 10 minutes expiry
        }
        
        # Delete any old OTP for this identifier
        db.otps.delete_many({"identifier": identifier})
        
        # Insert new OTP
        db.otps.insert_one(otp_record)
        
        # Send OTP via email
        if login_method == "email":
            email_body = f"""
            <html>
                <body style="font-family: Arial, sans-serif;">
                    <h2>Besties Craft - Login Verification</h2>
                    <p>Your OTP is: <strong style="font-size: 24px; color: #000;">{otp}</strong></p>
                    <p>This OTP will expire in 10 minutes.</p>
                    <p>If you didn't request this, please ignore this email.</p>
                </body>
            </html>
            """
            email_sent = send_email(identifier, "Besties Craft - Your OTP", email_body)
            
            if email_sent:
                return {
                    "success": True,
                    "message": f"OTP sent to {identifier}",
                    "detail": "Check your email for the OTP"
                }
            else:
                raise HTTPException(status_code=500, detail="Failed to send email")
        
        # For phone, you would integrate with Twilio or similar
        elif login_method == "phone":
            # For now, return success (in production, use Twilio)
            return {
                "success": True,
                "message": f"OTP sent to {identifier}",
                "detail": "Check your SMS for the OTP",
                "otp": otp  # Remove this in production!
            }
        
        else:
            raise HTTPException(status_code=400, detail="Invalid login method")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# VERIFY OTP
@app.post("/auth/verify-otp")
def verify_otp(data: dict):
    try:
        login_method = data.get("loginMethod")
        identifier = data.get(login_method)
        otp_entered = data.get("otp")
        
        if not all([login_method, identifier, otp_entered]):
            raise HTTPException(status_code=400, detail="Missing required fields")
        
        # Find OTP in database
        otp_record = db.otps.find_one({
            "identifier": identifier,
            "loginMethod": login_method
        })
        
        if not otp_record:
            raise HTTPException(status_code=401, detail="OTP not found or expired")
        
        # Check expiry
        if datetime.utcnow().timestamp() > otp_record.get("expiresAt", 0):
            db.otps.delete_one({"_id": otp_record["_id"]})
            raise HTTPException(status_code=401, detail="OTP has expired")
        
        # Verify OTP
        if otp_record["otp"] != otp_entered:
            raise HTTPException(status_code=401, detail="Invalid OTP")
        
        # Delete used OTP
        db.otps.delete_one({"_id": otp_record["_id"]})
        
        # Create or update user in database
        user_data = {
            "email" if login_method == "email" else "phone": identifier,
            "lastLogin": datetime.utcnow()
        }
        
        db.users.update_one(
            {"email" if login_method == "email" else "phone": identifier},
            {"$set": user_data},
            upsert=True
        )
        
        # Generate a simple token (in production, use JWT)
        import hashlib
        token = hashlib.sha256(f"{identifier}{datetime.utcnow()}".encode()).hexdigest()
        
        return {
            "success": True,
            "message": "Login successful",
            "token": token,
            "user": {
                login_method: identifier
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Admin Login
@app.post("/auth/admin-login")
def admin_login(credentials: dict):
    try:
        username = credentials.get("username")
        password = credentials.get("password")
        
        if username == "admin" and password == os.getenv("ADMIN_PASSWORD", "admin123"):
            return {
                "success": True,
                "message": "Login successful",
                "token": os.getenv("ADMIN_TOKEN", "your-secret-token")
            }
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
