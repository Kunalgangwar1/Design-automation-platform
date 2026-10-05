"""Create missing application tables without dropping existing data."""

from design_automation.database.connection import build_engine
from design_automation.database.schema import Base


def main() -> None:
    engine = build_engine()
    Base.metadata.create_all(engine)
    print(
        "Initialized tables: prompt_templates, generation_requests, generation_runs"
    )
    engine.dispose()


if __name__ == "__main__":
    main()
