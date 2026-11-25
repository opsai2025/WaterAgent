"""Load private configuration before application constants are evaluated."""
from pathlib import Path

from dotenv import load_dotenv


def load_settings(path=None):
    """Read the project .env without overriding explicit environment variables."""
    env_path = Path(path) if path is not None else Path(__file__).resolve().parent.parent / '.env'
    return load_dotenv(dotenv_path=env_path, override=False)
