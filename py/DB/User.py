# -*- coding: utf-8 -*-
# This file is part of Ecotaxa, see license.md in the application root directory for license informations.
# Copyright (C) 2015-2020  Picheral, Colin, Irisson (UPMC-CNRS)
#
from typing import List

from BO.helpers.TSVHelpers import none_to_empty
from .helpers import Session, Result
from .helpers.DDL import Column, ForeignKey, Sequence, Integer, String, Boolean, func
from .helpers.Direct import text
from .helpers.ORM import Model, relationship
from .helpers.Postgres import TIMESTAMP, CHAR


class User(Model):
    __tablename__ = 'users'
    id = Column(Integer, Sequence('seq_users'), primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    password = Column(String(255))
    name = Column(String(255), nullable=False)
    organisation = Column(String(255))
    active = Column(Boolean(), default=True)

    preferences = Column(String(40000))
    country = Column(String(50))

    usercreationdate = Column(TIMESTAMP, default=func.now())
    usercreationreason = Column(String(1000))

    # Mail status: 'V' for verified, 'W' for wrong
    mail_status = Column(CHAR, server_default=' ')
    # Date the mail status was set
    mail_status_date = Column(TIMESTAMP)

    # The relationships are created in Relations.py but the typing here helps the IDE
    roles: relationship
    # The projects that user has rights in, so he/she can participate at various levels.
    privs_on_projects: relationship
    # The objects of which _present_ classification was done by the user
    classified_objects: relationship
    # Preferences, per project, the global ones kept in field above.
    preferences_for_projects: relationship

    @staticmethod
    def find_users(session: Session, names: List[str], emails: List[str], found_users: dict):
        """
            Find the users in DB, by name or email.
            :param found_users: A dict in
        """
        sql = text("SELECT id, LOWER(name), LOWER(email) "
                   "  FROM users "
                   " WHERE LOWER(name) = ANY(:nms) or email = ANY(:ems) ")
        res: Result = session.execute(sql, {"nms": names, "ems": emails})
        for rec in res:
            for u in found_users:
                if u == rec[1] or none_to_empty(found_users[u].get('email')).lower() == rec[2]:
                    found_users[u]['id'] = rec[0]

    def has_role(self, role: str) -> bool:
        # TODO: Cache a bit. All roles are just python objects due to SQLAlchemy magic.
        return role in [r.name for r in list(self.roles)]

    def __str__(self):
        return "{0} ({1})".format(self.name, self.email)


class Role(Model):
    """
        The roles granted to users.
    """
    __tablename__ = 'roles'
    id = Column(Integer(), primary_key=True)  # ,Sequence('seq_roles')
    name = Column(String(80), unique=True, nullable=False)
    # The relationships are created in Relations.py but the typing here helps the IDE
    users: relationship

    APP_ADMINISTRATOR = "Application Administrator"
    USERS_ADMINISTRATOR = "Users Administrator"
    PROJECT_CREATOR = "Project creator"

    #    description = Column(String(255))
    def __str__(self):
        return "{0} ({1})".format(self.name, self.id)


class UserRole(Model):
    """
        Many-to-many relationship b/w User and Roles.
    """
    __tablename__ = 'users_roles'
    user_id = Column(Integer(), ForeignKey("users.id"), primary_key=True)
    role_id = Column(Integer(), ForeignKey("roles.id"), primary_key=True)


class Country(Model):
    """
        TODO: There is no FK from users
    """
    __tablename__ = 'countrylist'
    countryname = Column(String(50), primary_key=True, nullable=False)
