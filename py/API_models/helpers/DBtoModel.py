# -*- coding: utf-8 -*-
# This file is part of Ecotaxa, see license.md in the application root directory for license informations.
# Copyright (C) 2015-2020  Picheral, Colin, Irisson (UPMC-CNRS)
#
#  Attempt to map as automatically as possible the DB model into CRUD objects.
#
from typing import Type

from sqlalchemy import inspect
from sqlalchemy.orm import ColumnProperty

from helpers.pydantic import BaseConfig, BaseModel, create_model


class OrmConfig(BaseConfig):
    orm_mode = True


def sqlalchemy_to_pydantic(db_model: Type, *,
                           config: Type[BaseConfig] = OrmConfig,
                           exclude=None) -> Type[BaseModel]:
    if exclude is None:
        exclude = []
    mapper = inspect(db_model)
    fields = {}
    for attr in mapper.attrs:
        if isinstance(attr, ColumnProperty):
            if attr.columns:
                column = attr.columns[0]
                python_type = column.type.python_type
                name = attr.key
                if name in exclude:
                    continue
                default = None
                if column.default is None and not column.nullable:
                    default = ...
                fields[name] = (python_type, default)
    pydantic_model = create_model(
        db_model.__name__, __config__=config, **fields  # type: ignore
    )
    return pydantic_model
