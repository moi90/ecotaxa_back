import logging

# noinspection PyPackageRequirements
from starlette import status

from tests.credentials import ADMIN_AUTH

PROJECT_EXPORT_EMODNET_URL = "/export/emodnet?dry_run=False"

COLLECTION_CREATE_URL = "/collections/create"
COLLECTION_QUERY_URL = "/collections/{collection_id}"
COLLECTION_UPDATE_URL = "/collections/{collection_id}"
COLLECTION_DELETE_URL = "/collections/{collection_id}"


def test_create_collection(config, database, fastapi, caplog):
    caplog.set_level(logging.FATAL)

    # Admin imports the project
    from tests.test_import import test_import
    prj_id = test_import(config, database, caplog, "Collection project 1")

    # And creates a collection with it
    url = COLLECTION_CREATE_URL
    rsp = fastapi.post(url, headers=ADMIN_AUTH, json={"title": "Test collection",
                                                      "project_ids": [prj_id]})
    assert rsp.status_code == status.HTTP_200_OK
    coll_id = rsp.json()

    # Re-read
    url = COLLECTION_QUERY_URL.format(collection_id=coll_id)
    rsp = fastapi.get(url)
    # No admin, error
    assert rsp.status_code == status.HTTP_403_FORBIDDEN
    rsp = fastapi.get(url, headers=ADMIN_AUTH)
    assert rsp.status_code == status.HTTP_200_OK
    the_coll = rsp.json()
    assert the_coll == {'abstract': None,
                        'associates': [],
                        'citation': None,
                        'contact_user': None,
                        'creators': [],
                        'description': None,
                        'id': coll_id,
                        'license': None,
                        'project_ids': [prj_id],
                        'title': 'Test collection'}

    # Update the abstract
    url = COLLECTION_UPDATE_URL.format(collection_id=coll_id)
    the_coll['abstract'] = """
    A bit less abstract...
    """
    rsp = fastapi.put(url, headers=ADMIN_AUTH, json=the_coll)
    assert rsp.status_code == status.HTTP_200_OK

    # Delete the collection
    url = COLLECTION_DELETE_URL.format(collection_id=coll_id)
    rsp = fastapi.delete(url, headers=ADMIN_AUTH)
    assert rsp.status_code == status.HTTP_200_OK

    # Ensure it's gone
    url = COLLECTION_QUERY_URL.format(collection_id=coll_id)
    rsp = fastapi.get(url, headers=ADMIN_AUTH)
    assert rsp.status_code == status.HTTP_404_NOT_FOUND
