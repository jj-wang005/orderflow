"""V2.0-A additive configuration history and per-task snapshots. No order updates."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

CUSTOMER_FIELDS = ("customer_id", "customer_name", "sheet_name", "header_row", "field_mapping", "enabled", "config_version")
RULE_FIELDS = ("match_fields", "filter_conditions", "sort_config", "validation_config", "enabled", "rule_version")

def upgrade():
    op.create_table("configuration_version",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("customer_id", sa.String(64), sa.ForeignKey("customer_config.customer_id"), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.UniqueConstraint("customer_id", "config_version", name="uq_configuration_version_customer"),
        mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_bin")
    op.add_column("import_task", sa.Column("config_snapshot", sa.JSON(), nullable=True))
    bind = op.get_bind()
    # Frozen lightweight definitions; no imports of mutable application models.
    customers = sa.table("customer_config", *[sa.column(k, sa.JSON() if k == "field_mapping" else sa.Boolean() if k == "enabled" else sa.Integer() if k in {"header_row", "config_version"} else sa.String()) for k in CUSTOMER_FIELDS])
    rules = sa.table("match_rule", sa.column("customer_id", sa.String()), *[sa.column(k, sa.JSON() if k in {"match_fields", "filter_conditions", "sort_config", "validation_config"} else sa.Boolean() if k == "enabled" else sa.Integer()) for k in RULE_FIELDS])
    history = sa.table("configuration_version", sa.column("customer_id"), sa.column("config_version"), sa.column("rule_version"), sa.column("snapshot", sa.JSON()))
    tasks = sa.table("import_task", sa.column("customer_id"), sa.column("config_version"), sa.column("rule_version"), sa.column("config_snapshot", sa.JSON()))
    for customer in bind.execute(sa.select(customers)).mappings():
        active = list(bind.execute(sa.select(rules).where(rules.c.customer_id == customer["customer_id"], rules.c.enabled.is_(True))).mappings())
        if len(active) != 1:
            continue
        rule = active[0]
        snapshot = {"customer": dict(customer), "rule": {k: rule[k] for k in RULE_FIELDS}}
        bind.execute(history.insert().values(customer_id=customer["customer_id"], config_version=customer["config_version"], rule_version=rule["rule_version"], snapshot=snapshot))
        # Exact version pairs only: never invent an old configuration or change task outcomes.
        bind.execute(tasks.update().where(tasks.c.customer_id == customer["customer_id"], tasks.c.config_version == customer["config_version"], tasks.c.rule_version == rule["rule_version"]).values(config_snapshot=snapshot))

def downgrade():
    op.drop_column("import_task", "config_snapshot")
    op.drop_table("configuration_version")
