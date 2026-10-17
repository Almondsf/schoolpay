"""
Print the OpenAPI schema, for the frontend's generated types:

    python -m app.openapi > ../frontend/openapi.json

Works without a database or running server.
"""

import json
import sys

from app.main import create_app


def main() -> None:
    schema = create_app().openapi()
    json.dump(schema, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
