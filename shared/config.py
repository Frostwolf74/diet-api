import os

CONTAINER_NAME = os.environ.get("BLOB_CONTAINER", "datasets")
BLOB_SOURCE = os.environ.get("BLOB_SOURCE_NAME", "All_Diets.csv")
BLOB_CLEAN = os.environ.get("BLOB_CLEAN_NAME", "All_Diets_clean.csv")
BLOB_INSIGHTS = os.environ.get("BLOB_INSIGHTS_NAME", "insights_cache.json")
REDIS_KEY_INSIGHTS = os.environ.get("REDIS_INSIGHTS_KEY", "diet:insights:v1")

JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-in-production")
JWT_ALG = "HS256"
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "72"))

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:4280")

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GITHUB_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")

REDIS_CONNECTION_STRING = os.environ.get("REDIS_CONNECTION_STRING", "")

def _default_sqlite_path() -> str:
    if os.environ.get("WEBSITE_INSTANCE_ID"):
        return "/tmp/diet_users.db"
    return os.path.join(os.path.dirname(__file__), "..", "diet_users.db")


SQLITE_PATH = os.environ.get("SQLITE_PATH", _default_sqlite_path())
