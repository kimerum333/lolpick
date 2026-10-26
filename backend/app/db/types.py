"""Readable Python labels with compact numeric storage. Codebooks are append-only."""

from sqlalchemy import SmallInteger
from sqlalchemy.types import TypeDecorator
from app.db.compact import code_for, label_for


class CodeLabel(TypeDecorator):
    impl = SmallInteger
    cache_ok = True

    def __init__(self, group):
        self.group = group
        super().__init__()

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            return code_for(self.group, value)
        label_for(self.group, value)
        return value

    def process_result_value(self, value, dialect):
        return None if value is None else label_for(self.group, value)
