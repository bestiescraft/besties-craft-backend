from fastapi import FastAPI, APIRouter, HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from pydantic import BaseModel
from typing import Optional, List
import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId

# ===== LOAD ENV =====
load_dotenv()

# ===== FASTAPI APP =====
app = FastAPI(title="E-Commerce Backend")

# ===== CONFIG =====
JWT_SECRET = os.getenv("JWT_SECRET")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

MONGO_URL = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME")

# ===== MONGO CONNECT =====
client = AsyncIOMotorClient(MONGO_URL)
db = client[DATABASE_NAME]
products_collection = db["products"]

# ===== ROUTER =====
api_router = APIRouter()

# ===== SECURITY =====
security = HTTPBearer()

# ===== MODELS =====
class AdminLogin(BaseModel):
    password: str


class ProductCreate(BaseModel):
    name: str
    price: float
    description: Optional[str] = None
    image_url: Optional[str] = None


class Product(ProductCreate):
    id: str


# ===== TOKEN VERIFY =====
def verify_admin_token(
    credentials: HTTPAuthorizationCredentials = Security(security)
):
    token = credentials.credentials

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])

        if not payload.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin only")

        return payload

    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ===== ROOT =====
@app.get("/")
def root():
    return {"message": "API Running Successfully"}


# ===== ✅ HEALTH CHECK (NEW - FOR HOSTING) =====
@app.get("/health")
async def health():
    return {"status": "ok"}


# ===== ADMIN LOGIN =====
@api_router.post("/auth/admin-login")
async def admin_login(request: AdminLogin):

    if request.password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid password")

    token = jwt.encode(
        {
            "user_id": "admin",
            "is_admin": True,
            "exp": datetime.now(timezone.utc) + timedelta(days=1),
        },
        JWT_SECRET,
        algorithm="HS256",
    )

    return {"token": token}


# ===== GET ALL PRODUCTS =====
@api_router.get("/products", response_model=List[Product])
async def get_products():

    products = []
    async for product in products_collection.find():
        product["id"] = str(product["_id"])
        del product["_id"]
        products.append(product)

    return products


# ===== CREATE PRODUCT =====
@api_router.post(
    "/products",
    dependencies=[Depends(verify_admin_token)],
    response_model=Product
)
async def create_product(product: ProductCreate):

    product_dict = product.model_dump()
    result = await products_collection.insert_one(product_dict)

    product_dict["id"] = str(result.inserted_id)
    return product_dict


# ===== UPDATE PRODUCT =====
@api_router.put(
    "/products/{product_id}",
    dependencies=[Depends(verify_admin_token)]
)
async def update_product(product_id: str, product: ProductCreate):

    update_result = await products_collection.update_one(
        {"_id": ObjectId(product_id)},
        {"$set": product.model_dump()}
    )

    if update_result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")

    return {"message": "Product updated"}


# ===== DELETE PRODUCT =====
@api_router.delete(
    "/products/{product_id}",
    dependencies=[Depends(verify_admin_token)]
)
async def delete_product(product_id: str):

    delete_result = await products_collection.delete_one(
        {"_id": ObjectId(product_id)}
    )

    if delete_result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")

    return {"message": "Product deleted"}


# ===== INCLUDE ROUTER =====
app.include_router(api_router)
