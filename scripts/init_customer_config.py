"""Run from project root: python -m scripts.init_customer_config."""
import logging
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from app.core.database import get_session_factory
from app.repositories.customer_configuration import initialize_configurations, ConfigurationError
from app.services.customer_configuration import read_configuration_file

def main() -> int:
    try:
        configs = read_configuration_file()
        with get_session_factory()() as session, session.begin():
            initialize_configurations(session, configs)
        print("Customer configurations initialized: " + ", ".join(c.customer.customer_id for c in configs))
        return 0
    except (ValidationError, ConfigurationError, OSError, ValueError) as exc:
        logging.error("Configuration initialization rejected: %s", exc)
        return 1
    except SQLAlchemyError:
        logging.error("Database initialization failed; transaction rolled back. Check connection and existing configuration.")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
