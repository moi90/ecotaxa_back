# -*- coding: utf-8 -*-
# This file is part of Ecotaxa, see license.md in the application root directory for license informations.
# Copyright (C) 2015-2020  Picheral, Colin, Irisson (UPMC-CNRS)
#
import logging

import pytest
from API_operations.CRUD.Projects import ProjectsService
from API_operations.ObjectManager import ObjectManager

from tests.test_import import ADMIN_USER_ID

from tests.test_subset_merge import check_project


# Note: to go faster in a local dev environment, use "filled_database" instead of "database" below
# BUT DON'T COMMIT THE CHANGE
def test_purge_plain(database, caplog):
    caplog.set_level(logging.ERROR)
    from tests.test_import import test_import

    prj_id = test_import(database, caplog, "Test Purge")
    # Delete full
    with ProjectsService() as sce:
        sce.delete(current_user_id=ADMIN_USER_ID, prj_id=prj_id, only_objects=False)
    # Check it's gone
    with pytest.raises(AssertionError, match="Not found"):
        with ProjectsService() as sce:
            sce.delete(current_user_id=ADMIN_USER_ID, prj_id=prj_id, only_objects=False)


def test_purge_partial(database, caplog, tstlogs):
    caplog.set_level(logging.ERROR)
    from tests.test_import import test_import

    prj_id = test_import(database, caplog, "Test Purge partial")
    # Delete using wrong object IDs
    obj_ids = [500000 + i for i in range(15)]
    with ObjectManager() as sce:
        r = sce.delete(current_user_id=ADMIN_USER_ID, object_ids=obj_ids)
    assert r == (0, 0, 0, 0)
    check_project(tstlogs, prj_id)
