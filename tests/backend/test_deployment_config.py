"""容器环境覆盖 YAML 默认值与 PostgreSQL 驱动规范化的回归检查。"""
from backend.app.core.config import Settings
from types import SimpleNamespace


def test_container_environment_overrides_yaml_defaults(monkeypatch):
    monkeypatch.setenv("FRONTEND_SERVE_STATIC", "true")
    monkeypatch.setenv("SECRET_KEY", "deployment-test-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://demo:unused@db:5432/demo")
    settings = Settings(_env_file=None, frontend_serve_static=False, secret_key="yaml-secret")
    assert settings.frontend_serve_static is True
    assert settings.secret_key == "deployment-test-secret"
    assert settings.database_url == "postgresql+psycopg://demo:unused@db:5432/demo"


def test_migration_config_accepts_url_encoded_password(monkeypatch):
    from alembic import command
    from backend.app.core import database

    url = "postgresql+psycopg://demo:p%40ss@db:5432/demo"
    monkeypatch.setattr(database, "settings", SimpleNamespace(database_url=url))
    monkeypatch.setattr(database, "inspect", lambda _: SimpleNamespace(get_table_names=lambda: []))
    received = []
    monkeypatch.setattr(command, "upgrade", lambda config, _: received.append(config.get_main_option("sqlalchemy.url")))
    database._run_alembic_migrations()
    assert received == [url]
