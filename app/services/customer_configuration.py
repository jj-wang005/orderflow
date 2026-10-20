import json
from pathlib import Path
from pydantic import TypeAdapter
from app.schemas.customer_configuration import CustomerConfiguration

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = ROOT / "config" / "customer_templates.json"

def read_configuration_file(path: Path = DEFAULT_CONFIG_PATH) -> list[CustomerConfiguration]:
    return TypeAdapter(list[CustomerConfiguration]).validate_python(json.loads(path.read_text(encoding="utf-8")))
