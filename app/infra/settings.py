"""Configuration from the environment, including the database credentials.

Every value comes from an environment variable, or from a local .env file that git ignores.
No real value lives in the code. `database_url` and `jwt_secret` have no default on purpose:
the app refuses to start without them, which is safer than starting with a guessable one.

The database password travels inside the URL: postgresql+psycopg://user:password@host/db.
In production the variable is injected from a secrets store (AWS Secrets Manager, GitLab CI
variables), not from a file on the box.

Both secrets are `SecretStr`, so a stray print() or log line shows '**********' and not the value.
"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: SecretStr
    jwt_secret: SecretStr = Field(min_length=32)
    jwt_ttl_seconds: int = Field(default=3600, gt=0)
    log_level: str = "INFO"
