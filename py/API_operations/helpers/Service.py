# -*- coding: utf-8 -*-
# This file is part of Ecotaxa, see license.md in the application root directory for license informations.
# Copyright (C) 2015-2020  Picheral, Colin, Irisson (UPMC-CNRS)
#
import os
import typing
from typing import Optional, ContextManager

from DB.helpers.Connection import Connection, check_sqlalchemy_version
from helpers.AppConfig import Config
from sqlalchemy import event
from sqlalchemy.orm import Session


class BaseService(object):
    """
    A service, i.e. a stateless object which lives only for the time it does its job.
    """


#
# In postgresql.conf, postgresql has to listen to docker0 interface, typically:
#    listen_addresses = '127.0.0.1,172.17.0.1'       # what IP address(es) to listen on;
# as docker0 interface has 172.17.0.1 address
# Also in pg_hba.conf:
#    host    all             all             172.17.0.2/32           md5 or peer or trust
# as running docker processes are on 172.17.0.2
#
def _get_default_gateway():  # pragma: no cover
    # TODO: somewhere else
    for a_line in open("/proc/net/route").readlines():
        # Iface	Destination	Gateway 	Flags	RefCnt	Use	Metric	Mask		MTU	Window	IRTT
        # eth0	00000000	010011AC	0003	0	0	0	00000000	0	0	0
        fields = a_line.split()
        if fields[1] == "00000000":  # default route
            gw = fields[2]
            ip = [gw[i : i + 2] for i in range(6, -1, -2)]
            ip = [str(int(i, 16)) for i in ip]
            ip_str = ".".join(ip)
            return ip_str
    return ""


def _turn_localhost_for_docker(host: str):  # pragma: no cover
    """Turn localhost to the address as seen from inside the container
    For win & mac0s there is a solution, environment var host.docker.internal
     but https://github.com/docker/for-linux/issues/264
    """
    if host == "localhost" and os.getcwd().startswith("/app"):
        # noinspection PyBroadException
        try:
            return _get_default_gateway()
        except Exception:
            pass
    return host


class Service(BaseService, ContextManager):
    """
    A service for EcoTaxa. Supplies common useful features like:
        a DB session
        filesystem conventions
        logs redirection
    """

    the_config: Optional[Config] = None
    the_connection: Optional[Connection] = None
    the_readonly_connection: Optional[Connection] = None

    __slots__ = ["session", "ro_session", "config"]

    def __init__(self) -> None:
        # Use a single configuration
        if not Service.the_config:
            config: Config = Config()
            Service.the_config = config
        else:
            config = Service.the_config
        self.config: Config = config
        # Use a single r/w connection
        if not Service.the_connection:
            check_sqlalchemy_version()
            conn = self.build_connection(config)
            Service.the_connection = conn
        else:
            conn = Service.the_connection
        # Use a single read-only connection, with fallback to the r/w one
        if not Service.the_readonly_connection:
            if "RO_DB_HOST" in config.list_cnf():
                ro_conn = self.build_connection(config, True)
            else:
                ro_conn = conn
            Service.the_readonly_connection = ro_conn
        else:
            ro_conn = Service.the_readonly_connection
        # Finally feed the subclass
        self.session: Session = conn.get_session()
        if ro_conn != conn:
            self.ro_session: Session = ro_conn.get_session()
            # When r/w session commits, ensure the ro is clean.
            if not hasattr(self.session, "ro"):
                setattr(self.session, "ro", self.ro_session)
                event.listen(self.session, "before_commit", self.abort_ro)
        else:
            self.ro_session = self.session
        self._in_with: int = 0  # For debugging session leaks

    @staticmethod
    def build_connection(config: Config, read_only: bool = False):
        """
        Read a connection from the configuration.
        """
        host, port, db = config.get_db_address(read_only)
        host = _turn_localhost_for_docker(host)
        user, password = config.get_db_credentials(read_only)
        conn = Connection(
            host=host,
            port=port,
            db=db,
            user=user,
            password=password,
            read_only=read_only,
        )
        return conn

    @staticmethod
    def build_super_connection(config: Config, user: str, password: str):
        """
        Build a super-user connection from the configuration, directly to the DB server.
        """
        host, port, _db = config.get_db_address(False)
        host = _turn_localhost_for_docker(host)
        conn = Connection(
            host=host,
            port=port,
            db="postgres",
            user=user,
            password=password,
            read_only=False,
        )
        return conn

    @staticmethod
    def abort_ro(rw_session):
        """
        Raise if SQLAlchemy tries to flush(write) a readonly session.
        "Should not" happen in production if tests are covering OK.
        This does not manage plain SQL writing queries, which should be caught as the RO user has no
        relevant right on the tables.
        """
        try:
            ro_session = rw_session.ro
        except AttributeError:
            return
        # noinspection PyProtectedMember
        if ro_session._is_clean():
            return
        assert False, "Trying to ORM-write to a read-only session: %s" % str(
            ro_session.dirty
        )

    def close_db_sessions(self):
        # Release DB session
        if self.session is not None:
            # noinspection PyBroadException
            try:
                self.session.close()
            except Exception:
                # In this context we want no stack trace.
                pass
            # noinspection PyBroadException
            if self.ro_session != self.session:
                # noinspection PyBroadException
                try:
                    delattr(self.session, "ro")
                    self.ro_session.close()
                except Exception:
                    # In this context we want no stack trace.
                    pass
            self.session = None
            self.ro_session = None

    def __del__(self):
        # DB session should have been released during __exit__
        if self.session is not None:
            try:
                self.close_db_sessions()
            except:
                pass
            assert (
                False
            ), "%s: Please use Service-derived classes in a with() context" % str(self)

    def __enter__(self):
        self._in_with += 1
        return self

    def get_session(self):
        """Getter for having a session outside a with(),
        to use only in __init__() where there is no choice"""
        return self.session

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Release DB session
        self._in_with -= 1
        # assert self.in_with == 0 # To track issues
        self.close_db_sessions()

    @classmethod
    def re_init_after_fork(cls):
        cls.the_connection.engine.dispose()
        cls.the_connection = None
        cls.the_readonly_connection = None


if __name__ == "__main__":
    _get_default_gateway()
