from datetime import datetime, timedelta
import os
from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from database import get_user

JWT_SECRET_KEY = os.environ.get("SURVCAM_JWT_SECRET", "").strip()
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = int(os.environ.get("SURVCAM_ACCESS_TOKEN_EXPIRE_DAYS", "30"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str):
    return pwd_context.hash(password)


def verify_password(plain_password: str, password_hash: str):
    return pwd_context.verify(plain_password, password_hash)


def create_access_token(username: str, role: str):
    if len(JWT_SECRET_KEY) < 32 or JWT_SECRET_KEY.startswith("REPLACE_"):
        raise RuntimeError("Set SURVCAM_JWT_SECRET to a random value of at least 32 characters")
    expires = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)

    payload = {
        "sub": username,
        "role": role,
        "exp": expires,
    }

    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    access_token: str | None = Query(default=None),
):
    token = None

    if credentials is not None:
        token = credentials.credentials
    elif access_token:
        token = access_token

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        if len(JWT_SECRET_KEY) < 32 or JWT_SECRET_KEY.startswith("REPLACE_"):
            raise RuntimeError("Set SURVCAM_JWT_SECRET to a random value of at least 32 characters" )
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        username = payload.get("sub")
    except RuntimeError:
        raise
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not username:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = get_user(username)

    if user is None or user.get("is_active") != 1:
        raise HTTPException(status_code=401, detail="User inactive or not found")

    return user


def require_admin(current_user=Depends(get_current_user)):
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    return current_user
