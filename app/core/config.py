from functools import lru_cache
from pathlib import Path
from typing import Literal
import os
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True)
    db_host: str = "127.0.0.1"
    db_port: int = Field(default=3306, ge=1, le=65535)
    app_env: Literal['history','demo','test'] = 'history'
    db_name: Literal['order_automation','order_demo','order_test'] = 'order_automation'
    db_user: str = Field(min_length=1)
    db_password: SecretStr = Field(min_length=1)
    upload_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    ai_provider: Literal['mock', 'mimo'] = 'mock'
    mimo_api_key: SecretStr = SecretStr('')
    mimo_external_enabled: bool = False
    mimo_timeout_seconds: int = Field(default=30, ge=5, le=60)
    mimo_retries: int = Field(default=0, ge=0, le=1)
    ai_max_pdf_pages: int = Field(default=5, ge=1, le=10)
    ai_requests_per_minute: int = Field(default=20, ge=1, le=120)

    @model_validator(mode='after')
    def bind_database(self):
        expected={'history':'order_automation','demo':'order_demo','test':'order_test'}[self.app_env]
        if self.db_name!=expected:raise ValueError('APP_ENV 与目标数据库不一致，拒绝连接；不会回退其他环境')
        return self

    @property
    def database_url(self) -> URL:
        return URL.create("mysql+pymysql", username=self.db_user,
                          password=self.db_password.get_secret_value(), host=self.db_host,
                          port=self.db_port, database=self.db_name,
                          query={"charset": "utf8mb4"})

@lru_cache
def get_settings() -> Settings:
    profile=os.environ.get('APP_ENV','history')
    if profile not in {'history','demo','test'}:raise RuntimeError('APP_ENV 必须为 history/demo/test')
    path=ROOT / {'history':'.env','demo':'.env.demo','test':'.env.test'}[profile]
    if not path.is_file():raise RuntimeError('缺少所选环境配置文件；禁止回退 .env')
    from dotenv import dotenv_values
    declared=dotenv_values(path).get('APP_ENV',profile)
    if declared!=profile:raise RuntimeError('环境配置文件 APP_ENV 与启动环境不一致')
    return Settings(_env_file=path,app_env=profile)
