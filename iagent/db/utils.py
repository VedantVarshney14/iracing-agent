import os

import sqlalchemy
from sqlalchemy.orm import DeclarativeMeta


def get_engine() -> sqlalchemy.Engine:
    """Get database engine"""
    return sqlalchemy.create_engine(
        f"postgresql://{os.environ['DB_USERNAME']}:{os.environ['DB_PASSWORD']}@localhost:5432/simracing"
    )

def generate_ddl(model: DeclarativeMeta):
    """Generate DDL CREATE TABLE for a given SQLAlchemy model."""
    # Get the table name
    table_name = model.__tablename__

    # Get columns from the model
    columns = []
    for column in model.__table__.columns:
        col_name = column.name
        col_type = column.type

        # Map SQLAlchemy types to their corresponding SQL types
        if isinstance(col_type, sqlalchemy.Float):
            col_type_str = "DOUBLE"
        elif isinstance(col_type, sqlalchemy.Integer):
            col_type_str = "INT"
        elif isinstance(col_type, sqlalchemy.Boolean):
            col_type_str = "BOOLEAN"
        elif isinstance(col_type, sqlalchemy.String):
            col_type_str = "VARCHAR"
        elif isinstance(col_type, sqlalchemy.TIMESTAMP):
            col_type_str = "TIMESTAMP"
        else:
            col_type_str = str(col_type)

        columns.append(f"{col_name} {col_type_str}")

    # Generate the CREATE TABLE statement
    ddl = f"CREATE TABLE IF NOT EXISTS {table_name} (\n"
    ddl += ",\n".join(columns)
    ddl += "\n);"

    return sqlalchemy.text(ddl)