from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import CustomerConfig, MatchRule, ConfigurationVersion
from app.schemas.customer_configuration import CustomerConfiguration
from app.services.source_columns import configuration_columns

class ConfigurationError(ValueError):
    pass

def load_configuration(session: Session, customer_id: str, *, for_update: bool = False) -> CustomerConfiguration:
    customer_query = select(CustomerConfig).where(CustomerConfig.customer_id == customer_id, CustomerConfig.enabled.is_(True)).execution_options(populate_existing=True)
    customer = session.scalar(customer_query.with_for_update() if for_update else customer_query)
    if customer is None:
        raise ConfigurationError("Customer configuration is missing or disabled")
    rules_query = select(MatchRule).where(MatchRule.customer_id == customer_id, MatchRule.enabled.is_(True)).execution_options(populate_existing=True)
    rules = session.scalars(rules_query.with_for_update() if for_update else rules_query).all()
    if len(rules) != 1:
        raise ConfigurationError("Exactly one enabled rule is required per customer")
    rule = rules[0]
    return CustomerConfiguration.model_validate({
        "source_columns": configuration_columns(session, customer)[0],
        "customer": {key: getattr(customer, key) for key in CustomerConfiguration.model_fields["customer"].annotation.model_fields},
        "rule": {key: getattr(rule, key) for key in CustomerConfiguration.model_fields["rule"].annotation.model_fields},
    })

def initialize_configurations(session: Session, configs: list[CustomerConfiguration]) -> None:
    """Caller owns transaction. Lock customer records to serialize same-customer updates.

    Existing changed definitions require a version increment, never silent overwrite.
    """
    # Validate again at the write boundary, including programmatically modified models.
    configs = [CustomerConfiguration.model_validate(c.model_dump(mode="json")) for c in configs]
    if len({c.customer.customer_id for c in configs}) != len(configs):
        raise ConfigurationError("Duplicate customer IDs in initialization input")
    for config in sorted(configs, key=lambda c: c.customer.customer_id):
        customer_data = config.customer.model_dump(mode="json")
        rule_data = config.rule.model_dump(mode="json")
        customer = session.scalar(select(CustomerConfig).where(CustomerConfig.customer_id == config.customer.customer_id).with_for_update())
        if customer is None:
            customer = CustomerConfig(**customer_data)
            session.add(customer)
            session.flush()
        else:
            old = {key: getattr(customer, key) for key in customer_data}
            if old != customer_data:
                if config.customer.config_version <= customer.config_version:
                    raise ConfigurationError("Changed customer configuration requires a higher config_version")
                for key, value in customer_data.items():
                    setattr(customer, key, value)
        rules = session.scalars(select(MatchRule).where(MatchRule.customer_id == config.customer.customer_id).with_for_update()).all()
        same_version = [r for r in rules if r.rule_version == config.rule.rule_version]
        if len(same_version) > 1 or len([r for r in rules if r.enabled]) > 1:
            raise ConfigurationError("Duplicate rule versions or multiple enabled rules; resolve explicitly")
        if same_version:
            old = {key: getattr(same_version[0], key) for key in rule_data}
            if old != rule_data:
                raise ConfigurationError("Changed rule requires a new rule_version")
        else:
            if rules and config.rule.rule_version <= max(r.rule_version for r in rules):
                raise ConfigurationError("New rule version must increase")
            for rule in rules:
                rule.enabled = False
            session.add(MatchRule(customer_id=config.customer.customer_id, **rule_data))
        session.flush()


def task_configuration(session: Session, task) -> CustomerConfiguration:
    """Never silently apply a later configuration to an existing task."""
    if task.config_snapshot is not None:
        config = CustomerConfiguration.model_validate(task.config_snapshot)
    else:
        # Resolve a snapshotless legacy task from its exact immutable receipt.
        # Never rewrite the task or fall back to a newer version's semantics.
        version = session.scalar(select(ConfigurationVersion).where(
            ConfigurationVersion.customer_id == task.customer_id,
            ConfigurationVersion.config_version == task.config_version,
            ConfigurationVersion.rule_version == task.rule_version))
        config = (CustomerConfiguration.model_validate(version.snapshot) if version is not None
                  else load_configuration(session, task.customer_id))
    if (config.customer.customer_id, config.customer.config_version, config.rule.rule_version) != (task.customer_id, task.config_version, task.rule_version):
        raise ConfigurationError("Task configuration snapshot/version is unavailable or inconsistent")
    return config
