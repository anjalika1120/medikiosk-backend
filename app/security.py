from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

pwd_hash = PasswordHash((BcryptHasher(),))

def hash_password(password: str) -> str:
    return pwd_hash.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_hash.verify(plain_password, hashed_password)
