from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime
import os

# MongoDB Connection
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
client = MongoClient(MONGO_URI)
db = client["besties"]

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

# FIX STOCK - Temporary endpoint to add stock to existing products
@app.put("/products/{product_id}/add-stock")
def add_stock_field(product_id: str):
    try:
        product_id = ObjectId(product_id)
        
        result = db.products.update_one(
            {"_id": product_id},
            {
                "$set": {
                    "stock": 10,
                    "inStock": True,
                    "updatedAt": datetime.utcnow()
                }
            }
        )
        
        if result.matched_count == 0:
            return {"error": "Product not found"}, 404
        
        return {"message": "Stock fields added", "modified_count": result.modified_count}
    except Exception as e:
        return {"error": str(e)}, 500

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
