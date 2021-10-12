# -*- coding: utf-8 -*-
# This file is part of Ecotaxa, see license.md in the application root directory for license informations.
# Copyright (C) 2015-2020  Picheral, Colin, Irisson (UPMC-CNRS)
#
# Based on https://fastapi.tiangolo.com/
#
import os
from logging import INFO
from typing import Union, Tuple

from fastapi import FastAPI, Request, Response, status, Depends, HTTPException, UploadFile, File, Query, Form, Body, \
    Path
from fastapi import responses
from fastapi.logger import logger as fastapi_logger
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi_utils.timing import add_timing_middleware
from sqlalchemy.sql.expression import null

from API_models.constants import Constants
from API_models.crud import *
from API_models.exports import EMODnetExportRsp, ExportRsp, ExportReq
from API_models.filesystem import DirectoryModel
from API_models.helpers.Introspect import plain_columns
from API_models.imports import *
from API_models.login import LoginReq
from API_models.merge import MergeRsp
from API_models.objects import ObjectSetQueryRsp, ObjectSetRevertToHistoryRsp, ClassifyReq, ObjectModel, \
    ObjectHeaderModel, HistoricalClassificationModel, ObjectSetSummaryRsp, ClassifyAutoReq
from API_models.prediction import PredictionRsp, PredictionReq
from API_models.subset import SubsetReq, SubsetRsp
from API_models.taxonomy import TaxaSearchRsp, TaxonModel, TaxonomyTreeStatus, TaxonUsageModel
from API_operations.CRUD.Collections import CollectionsService
from API_operations.CRUD.Constants import ConstantsService
from API_operations.CRUD.Instruments import InstrumentsService
from API_operations.CRUD.Jobs import JobCRUDService
from API_operations.CRUD.Object import ObjectService
from API_operations.CRUD.ObjectParents import SamplesService, AcquisitionsService, ProcessesService
from API_operations.CRUD.Projects import ProjectsService
from API_operations.CRUD.Users import UserService
from API_operations.Consistency import ProjectConsistencyChecker
from API_operations.DBSyncService import DBSyncService
from API_operations.JsonDumper import JsonDumper
from API_operations.Merge import MergeService
from API_operations.ObjectManager import ObjectManager
from API_operations.Prediction import PredictForProject, CNNForProject
from API_operations.Stats import ProjectStatsFetcher
from API_operations.Status import StatusService
from API_operations.Subset import SubsetServiceOnProject
from API_operations.TaxoManager import TaxonomyChangeService, CentralTaxonomyService
from API_operations.TaxonomyService import TaxonomyService
from API_operations.UserFolder import UserFolderService, CommonFolderService
from API_operations.admin.ImageManager import ImageManagerService
from API_operations.admin.NightlyJob import NightlyJobService
from API_operations.exports.EMODnet import EMODnetExport
from API_operations.exports.ForProject import ProjectExport
from API_operations.imports.Import import FileImport
from API_operations.imports.SimpleImport import SimpleImport
from BG_operations.JobScheduler import JobScheduler
from BO.Acquisition import AcquisitionBO
from BO.Classification import HistoricalClassification, ClassifIDT
from BO.Job import JobBO
from BO.Object import ObjectBO
from BO.ObjectSet import ObjectIDListT
from BO.Preferences import Preferences
from BO.Process import ProcessBO
from BO.Project import ProjectBO, ProjectUserStats
from BO.Rights import RightsBO
from BO.Sample import SampleBO
from BO.Taxonomy import TaxonBO
from DB import ProjectPrivilege
from DB.Project import ProjectTaxoStat
from helpers.Asyncio import async_bg_run, log_streamer
from helpers.DynamicLogs import get_logger
from helpers.fastApiUtils import internal_server_error_handler, dump_openapi, get_current_user, RightsThrower, \
    get_optional_current_user, MyORJSONResponse, ValidityThrower
from helpers.login import LoginService
from helpers.pydantic import sort_and_prune

# from fastapi.middleware.gzip import GZipMiddleware

logger = get_logger(__name__)
# TODO: A nicer API doc, see https://github.com/tiangolo/fastapi/issues/1140

fastapi_logger.setLevel(INFO)

app = FastAPI(title="EcoTaxa",
              version="0.0.20",
              # openapi URL as seen from navigator, this is included when /docs is required
              # which serves swagger-ui JS app. Stay in /api sub-path.
              openapi_url="/api/openapi.json",
              servers=[
                  {"url": "/api", "description": "External access"},
                  {"url": "/", "description": "Local access"},
              ],
              default_response_class=MyORJSONResponse
              # For later: Root path is in fact _removed_ from incoming requests, so not relevant here
              )

# Instrument a bit
add_timing_middleware(app, record=logger.info, prefix="app", exclude="untimed")

# Optimize large responses
# app.add_middleware(GZipMiddleware, minimum_size=1024)

# HTML stuff
# app.mount("/styles", StaticFiles(directory="pages/styles"), name="styles")
templates = Jinja2Templates(directory=os.path.dirname(__file__) + "/pages/templates")
# Below is useless if proxied by legacy app
CDNs = " ".join(["cdn.datatables.net"])
CRSF_header = {
    'Content-Security-Policy': "default-src 'self' 'unsafe-inline' 'unsafe-eval' "
                               f"blob: data: {CDNs};frame-ancestors 'self';form-action 'self';"
}

# Establish second routes via /api to same app
app.mount("/api", app)


# noinspection PyUnusedLocal
@app.post(
    "/login",
    tags=['authentification'],
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": "eyJ1c2VyX2lkIjo5OTN9.YUmHHw.-X4tsLsYbwldKL6vDgO3o4-aAxE"
                }
            }
        }
    },
    response_model=str
)
async def login(params: LoginReq = Body(...)) -> str:
    """
        **Login barrier,** 
        
        If successful, the login will returns a **JWT** which will have to be used
        in bearer authentication scheme for subsequent calls.
    """
    with LoginService() as sce:
        with RightsThrower():
            return sce.validate_login(params.username, params.password)


@app.get("/users", tags=['users'], response_model=List[UserModel])
def get_users(current_user: int = Depends(get_current_user)):
    """
        Returns the list of **all users** with their information. 

        🔒 *For admins only.*
    """
    with UserService() as sce:
        return sce.list(current_user)


@app.get("/users/me", tags=['users'], response_model=UserModelWithRights)
def show_current_user(current_user: int = Depends(get_current_user)):
    """
        Returns **currently authenticated user's** (i.e. you) information, permissions and last used projects.
    """
    with UserService() as sce:
        ret = sce.search_by_id(current_user, current_user)
        assert ret is not None
        # noinspection PyTypeHints
        ret.can_do = RightsBO.allowed_actions(ret)  # type:ignore
        # noinspection PyTypeHints
        ret.last_used_projects = Preferences(ret).recent_projects(session=sce.session)  # type:ignore
        return ret


@app.get(
    "/users/my_preferences/{project_id}",
    tags=['users'],
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": "{\"dispfield\": \" dispfield_orig_id dispfield_classif_auto_score dispfield_classif_when\", \"ipp\": \"1000\", \"magenabled\": \"1\", \"popupenabled\": \"1\", \"sortby\": \"orig_id\", \"sortorder\": \"asc\", \"statusfilter\": \"P\", \"zoom\": \"90\"}"
                }
            }
        }
    },
    response_model=str)
def get_current_user_prefs(
        project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
        key: str = Query(..., title="Key", description="The preference key, as text.",
                         example="filters"),
        current_user: int = Depends(get_current_user)) -> str:
    """
        **Returns one preference**, for a project and the currently authenticated user.

        Available keys are **cwd**, **img_import** and **filters**.
    """
    with UserService() as sce:
        return sce.get_preferences_per_project(current_user, project_id, key)


@app.put("/users/my_preferences/{project_id}", tags=['users'],
         responses={
             200: {
                 "content": {
                     "application/json": {
                         "example": null
                     }
                 }
             }
         })
def set_current_user_prefs(
        project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
        key: str = Query(..., title="Key", description="The preference key, as text.",
                         example="filters"),
        value: str = Query(..., title="Value",
                           description="The value to set this preference to, as text.",
                           example="{\"dispfield\": \" dispfield_orig_id dispfield_classif_auto_score dispfield_classif_when dispfield_random_value\", \"ipp\": \"500\", \"magenabled\": \"1\", \"popupenabled\": \"1\", \"sortby\": \"orig_id\", \"sortorder\": \"asc\", \"statusfilter\": \"\", \"zoom\": \"90\"}"),
        current_user: int = Depends(get_current_user)):
    """
        **Sets one preference**, for a project and for the currently authenticated user.

        Available keys are **cwd**, **img_import** and **filters**.

        The key disappears if set to empty string.

        **Returns NULL upon success.**
    """
    with UserService() as sce:
        return sce.set_preferences_per_project(current_user, project_id, key, value)


@app.get("/users/search", tags=['users'], response_model=List[UserModel])
def search_user(current_user: int = Depends(get_current_user),
                by_name: Optional[str] = Query(default=None, title="search by name",
                                               description="Search by name, use % for searching with 'any char'.",
                                               example="%userNa%")):
    """
        **Search users using various criteria**, search is case insensitive and might contain % chars.
    """
    with UserService() as sce:
        ret = sce.search(current_user, by_name)
    return ret


@app.get("/users/{user_id}", tags=['users'], response_model=UserModel)
def get_user(user_id: int = Path(..., description="Internal, the unique numeric id of this user.", example=1),
             current_user: int = Depends(get_current_user)):
    """
        Returns **information about the user** corresponding to the given id.
    """
    with UserService() as sce:
        ret = sce.search_by_id(current_user, user_id)
    if ret is None:
        raise HTTPException(status_code=404, detail="User not found")
    return ret


# ######################## END OF USER

@app.post("/collections/create",
          tags=['collections'],
          responses={
              200: {
                  "content": {
                      "application/json": {
                          "example": 1
                      }
                  }
              }
          },
          response_model=int)
def create_collection(params: CreateCollectionReq = Body(...),
                      current_user: int = Depends(get_current_user)) -> Union[int, str]:
    """
        **Create a collection** with at least one project inside.

        Returns the created collection Id.

        🔒 *For admins only.*
    """
    with CollectionsService() as sce:
        with RightsThrower():
            ret = sce.create(current_user, params)
    if isinstance(ret, str):
        raise HTTPException(status_code=404, detail=ret)
    # TODO: Mettre les syncs dans les services, moins dégeu
    return ret


@app.get("/collections/search", tags=['collections'], response_model=List[CollectionModel])
def search_collections(title: str = Query(..., title="Title",
                                          description="Search by title, use % for searching with 'any char'.",
                                          example="%coll%"),
                       current_user: int = Depends(get_current_user)):
    """
        **Search for collections.**
        
        🔒 *For admins only.*
    """
    with CollectionsService() as sce:
        with RightsThrower():
            matching_collections = sce.search(current_user, title)
    return matching_collections


@app.get("/collections/by_title", tags=['collections'], response_model=CollectionModel)
def collection_by_title(
        q: str = Query(..., title="Title", description="Search by **exact** title.", example="My collection")):
    """
        Return the **single collection with this title**.
        
        *For published datasets.*

        ⚠️ DO NOT MODIFY BEHAVIOR ⚠️ 
    """
    with CollectionsService() as sce:
        with RightsThrower():
            matching_collection = sce.query_by_title(q)
    return matching_collection


@app.get("/collections/by_short_title", tags=['collections'], response_model=CollectionModel)
def collection_by_short_title(
        q: str = Query(..., title="Short title", description="Search by **exact** short title.",
                       example="My coll")):
    """
        Return the **single collection with this short title**.

        *For published datasets.*

        ⚠️ DO NOT MODIFY BEHAVIOR ⚠️ 
    """
    with CollectionsService() as sce:
        with RightsThrower():
            matching_collection = sce.query_by_short_title(q)
    return matching_collection


@app.get("/collections/{collection_id}", tags=['collections'], response_model=CollectionModel)
def get_collection(
        collection_id: int = Path(..., description="Internal, the unique numeric id of this collection.",
                                  example=1),
        current_user: int = Depends(get_current_user)):
    """
       Returns **information about the collection** corresponding to the given id.

        🔒 *For admins only.*
    """
    with CollectionsService() as sce:
        with RightsThrower():
            present_collection = sce.query(current_user, collection_id, for_update=False)
        if present_collection is None:
            raise HTTPException(status_code=404, detail="Collection not found")
        return present_collection


@app.put("/collections/{collection_id}", tags=['collections'],
         responses={
             200: {
                 "content": {
                     "application/json": {
                         "example": null
                     }
                 }
             }
         })
def update_collection(collection: CollectionModel = Body(...),
                      collection_id: int = Path(..., description="Internal, the unique numeric id of this collection.",
                                                example=1),
                      current_user: int = Depends(get_current_user)):
    """
       **Update the collection**. Note that some updates are silently failing when not compatible
        with the composing projects.

        **Returns NULL upon success.**

        🔒 *For admins only.*
    """
    with CollectionsService() as sce:
        with RightsThrower():
            present_collection = sce.query(current_user, collection_id, for_update=True)
        if present_collection is None:
            raise HTTPException(status_code=404, detail="Collection not found")
        # noinspection PyUnresolvedReferences
        present_collection.update(session=sce.session,
                                  title=collection.title,
                                  short_title=collection.short_title,
                                  project_ids=collection.project_ids,
                                  provider_user=collection.provider_user, contact_user=collection.contact_user,
                                  citation=collection.citation, abstract=collection.abstract,
                                  description=collection.description,
                                  creator_users=collection.creator_users, associate_users=collection.associate_users,
                                  creator_orgs=collection.creator_organisations,
                                  associate_orgs=collection.associate_organisations)


@app.get("/collections/{collection_id}/export/emodnet", tags=['collections'], response_model=EMODnetExportRsp)
def emodnet_format_export(
        collection_id: int = Path(..., description="Internal, the unique numeric id of this collection.",
                                  example=1),
        dry_run: bool = Query(..., title="Dry run",
                              description="If set, then only a diagnostic of doability will be done.",
                              example=False),
        with_zeroes: bool = Query(..., title="With zeroes",
                                  description="If set, then *absent* records will be generated, in the relevant samples, for categories present in other samples.",
                                  example=False),
        auto_morpho: bool = Query(..., title="Auto morpho",
                                  description="If set, then any object classified on a Morpho category will be added to the count of the nearest Phylo parent, upward in the tree.",
                                  example=False),
        with_computations: bool = Query(..., title="With computations",
                                        description="If set, then an attempt will be made to compute organisms concentrations and biovolumes.",
                                        example=False),
        current_user: int = Depends(get_current_user)) -> EMODnetExportRsp:
    """
        **Export the collection in EMODnet format**, @see https://www.emodnet-ingestion.eu

        Produces a DwC-A archive into a temporary directory, ready for download.

        Maybe useful, a reader in Python: https://python-dwca-reader.readthedocs.io/en/latest/index.html

        🔒 *For admins only.*
    """
    with EMODnetExport(collection_id, dry_run, with_zeroes, with_computations, auto_morpho) as sce:
        with RightsThrower():
            return sce.run(current_user)


@app.delete("/collections/{collection_id}", tags=['collections'],
            responses={
                200: {
                    "content": {
                        "application/json": {
                            "example": 0
                        }
                    }
                }
            },
            response_model=int)
def erase_collection(
        collection_id: int = Path(..., description="Internal, the unique numeric id of this collection.",
                                  example=1),
        current_user: int = Depends(get_current_user)) -> int:
    """
        **Delete the collection**, 
        
        i.e. the precious fields, as the projects are just linked-at from the collection.
        
        🔒 *For admins only.*
    """
    with CollectionsService() as sce:
        with RightsThrower():
            return sce.delete(current_user, collection_id)


# ######################## END OF COLLECTION

MyORJSONResponse.register(ProjectBO, ProjectModel)
MyORJSONResponse.register(User, UserModel)

project_model_columns = plain_columns(ProjectModel)

#TODO JCE - description
# TODO TODO TODO: No verification of GET query parameters by FastAPI. pydantic does POST models OK.
@app.get("/projects/search", tags=['projects'], response_model=List[ProjectModel])
def search_projects(current_user: Optional[int] = Depends(get_optional_current_user),
                    also_others: bool = Query(default=False, deprecated=True, title="Also others", description="",
                                              example=False),
                    not_granted: bool = Query(default=False, title="Not granted",
                                              description="Return projects on which the current user has _no permission_, but visible to him/her.",
                                              example=False),
                    for_managing: bool = Query(default=False, title="For managing",
                                               description="Return projects that can be written to (including erased) by the current user.",
                                               example=False),
                    title_filter: str = Query(default="", title="Title filter",
                                              description="Use this pattern for matching returned projects names.",
                                              example="Tara"),
                    instrument_filter: str = Query(default="", title="Instrument filter",
                                                   description="Only return projects where this instrument was used.",
                                                   example="uvp5"),
                    filter_subset: bool = Query(default=False, title="Filter subset",
                                                description="Only return projects having 'subset' in their names.",
                                                example=True),
                    order_field: Optional[str] = Query(default=None, title="Order field",
                                                       description="One of %s" % list(project_model_columns.keys()),
                                                       example="instrument"),
                    window_start: Optional[int] = Query(default=None, title="Window start",
                                                        description="Skip `window_start` before returning data.",
                                                        example="0"),
                    window_size: Optional[int] = Query(default=None, title="Window size",
                                                       description="Return only `window_size` lines.", example="100"),
                    ) -> MyORJSONResponse:  # List[ProjectBO]
    """
        Returns **projects which the current user has explicit permission to access, with search options.**

        Note that, for performance reasons, in returned ProjectModels, field 'highest_rank' is NOT valued
        (unlike in simple query). The same information can be found in 'managers', 'annotators' and 'viewers' lists.
    """
    not_granted = not_granted or also_others
    with ProjectsService() as sce:
        ret = sce.search(current_user_id=current_user, not_granted=not_granted, for_managing=for_managing,
                         title_filter=title_filter, instrument_filter=instrument_filter, filter_subset=filter_subset)
    # The DB query takes a few ms, and enrich not much more, so we can afford to narrow the search on the result
    ret = sort_and_prune(ret, order_field, project_model_columns, window_start, window_size)
    return MyORJSONResponse(ret)


@app.post("/projects/create", tags=['projects'],
          responses={
              200: {
                  "content": {
                      "application/json": {
                          "example": 44
                      }
                  }
              }
          },
          response_model=int)
def create_project(params: CreateProjectReq = Body(...),
                   current_user: int = Depends(get_current_user)) -> Union[int, str]:
    """
        **Create an empty project with only a title,** and **return the numeric id of this newly created project**.

        The project will be managed by current user.

        🔒 The user has to be *app administrator* or *project creator*.
    """
    with ProjectsService() as sce:
        with RightsThrower():
            ret = sce.create(current_user, params)
    if isinstance(ret, str):
        raise HTTPException(status_code=404, detail=ret)
    with DBSyncService(Project, Project.projid, ret) as ssce: ssce.wait()
    return ret


@app.post("/projects/{project_id}/subset", tags=['projects'], response_model=SubsetRsp)
def project_subset(project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
                   params: SubsetReq = Body(...),
                   current_user: int = Depends(get_current_user)):
    """
        **Subset a project into another one.**
    """
    with SubsetServiceOnProject(project_id, params) as sce:
        with RightsThrower():
            ret = sce.run(current_user)
    return ret


@app.get("/projects/{project_id}", tags=['projects'], response_model=ProjectModel)
def project_query(project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
                  for_managing: Optional[bool] = Query(title="For managinig", description="For managing this project.",
                                                       default=False, example=False),
                  current_user: Optional[int] = Depends(get_optional_current_user)) -> ProjectBO:
    """
        **Returns project** if it exists for current user, eventually for managing it.
    """
    with ProjectsService() as sce:
        for_managing = bool(for_managing)
        with RightsThrower():
            ret = sce.query(current_user, project_id, for_managing, for_update=False)
        return ret


@app.get("/project_set/taxo_stats", tags=['projects'], response_model=List[ProjectTaxoStatsModel])  # type: ignore
def project_set_get_stats(ids: str = Query(..., title="Ids",
                                           description="String containing the list of one or more id separated by non-num char. \n \n **If several ids are provided**, one stat record will be returned per project.",
                                           example="1"),
                          taxa_ids: Optional[str] = Query(title="Taxa Ids",
                                                          description="**If several taxa_ids are provided**, one stat record will be returned per requested taxa, if populated.\n \n **If taxa_ids is all**, all valued taxa in the project(s) are returned.",
                                                          default="", example="all"),
                          current_user: Optional[int] = Depends(get_optional_current_user)
                          ) -> MyORJSONResponse:  # List[ProjectTaxoStats]
    """
        **Returns projects statistics**, i.e. used taxa and classification states.
    """
    with ProjectsService() as sce:
        num_prj_ids = _split_num_list(ids)
        if taxa_ids == 'all':
            num_taxa_ids = taxa_ids
        else:
            num_taxa_ids = _split_num_list(taxa_ids)
        with RightsThrower():
            ret = sce.read_stats(current_user, num_prj_ids, num_taxa_ids)
        return MyORJSONResponse(ret)


@app.get("/project_set/user_stats", tags=['projects'],
         responses={
             200: {
                 "content": {
                     "application/json": {
                         "example": [{
                             "projid": 1,
                             "annotators": [{
                                 "id": 1267,
                                 "name": "User Name"
                             }],
                             "activities": [{
                                 "id": 1267,
                                 "nb_actions": 605,
                                 "last_annot": "2021-09-27T13:08:54"
                             }]
                         }]
                     }
                 }
             }
         }, response_model=List[ProjectUserStatsModel])  # type: ignore
def project_set_get_user_stats(ids: str = Query(..., title="Ids",
                                                description="String containing the list of one or more id separated by non-num char. \n \n **If several ids are provided**, one stat record will be returned per project.",
                                                example="1"),
                               current_user: int = Depends(get_current_user)) -> List[ProjectUserStats]:
    """
        **Returns projects user statistics**, i.e. a summary of the work done by users in the
        required projects. 
        
        The returned values are a detail per project, so size of input list equals size of output list.
    """
    with ProjectsService() as sce:
        num_ids = _split_num_list(ids)
        with RightsThrower():
            ret = sce.read_user_stats(current_user, num_ids)
        return ret


@app.get("/project_set/column_stats", tags=['projects'],
         responses={
             200: {
                 "content": {
                     "application/json": {
                         "example": {"proj_ids": [1040, 4702],
                                     "total": 54169,
                                     "columns": ["fre.area", "obj.depth_min"],
                                     "counts": [54169, 54169],
                                     "variances": [1895031198.64, 0.000258]}
                     }
                 }
             }
         }, response_model=ProjectSetColumnStatsModel)  # type: ignore
def project_set_get_column_stats(ids: str = Query(..., title="Project ids",
                                                  description="String containing the list of one or more id separated by non-num char.",
                                                  example="1400+1453"),
                                 names: str = Query(..., title="Column names",
                                                    description="Coma-separated prefixed columns, on which stats are needed.",
                                                    example="fre.area,obj.depth_min,fre.nb2"),
                                 current_user: int = Depends(get_current_user)
                                 ) -> ProjectSetColumnStats:
    """
        **Returns projects validated data statistics**, for all named columns, in all given projects.

        The free columns here are named by the alias e.g. 'area', not technical name e.g. 'n43'.

        This allows getting stats on projects with different mappings, but common names.
    """
    with ProjectsService() as sce:
        num_ids = _split_num_list(ids)
        name_list = names.split(",")
        with RightsThrower():
            ret = sce.read_columns_stats(current_user, num_ids, name_list)
        return ret


@app.post("/projects/{project_id}/dump", tags=['projects'], include_in_schema=False)  # pragma:nocover
def project_dump(project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
                 filters: ProjectFiltersModel = Body(...),
                 current_user: int = Depends(get_current_user)):
    """
        Dump the project in JSON form. Internal so far.
    """
    # TODO: Use a StreamingResponse to avoid buffering
    with JsonDumper(current_user, project_id, filters) as sce:
        # TODO: Finish. lol.
        import sys
        return sce.run(sys.stdout)


@app.post("/projects/{project_id}/merge", tags=['projects'], response_model=MergeRsp)
def project_merge(project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
                  source_project_id: int = Query(..., title="Source project Id",
                                                 description="Id of the other project. All objects from this source project will be moved to the project_id above and the source project itself will be deleted.",
                                                 example=2),
                  dry_run: bool = Query(..., title="Dry run",
                                        description="If set, then only a diagnostic of doability will be done.",
                                        example=True),
                  current_user: int = Depends(get_current_user)) -> MergeRsp:
    """
        **Merge another project into this one.**
        
        It's more a phagocytosis than a merge, as all objects from this source project will
        be moved to the project_id above and the source project itself will be deleted.

        TODO: Explain a bit with it might fail (too many free columns, unique orig_ids collision)
    """
    with MergeService(project_id, source_project_id, dry_run) as sce:
        with RightsThrower():
            return sce.run(current_user)


@app.get("/projects/{project_id}/check", tags=['projects'],
         responses={
             200: {
                 "content": {
                     "application/json": {
                         "example": ["Acquisition '765' is nested in several samples: [1234,7697]", "Acquisition '766' has no associated Process "]
                     }
                 }
             }
         },
         response_model=List[str]
         )
def project_check(project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
                  current_user: int = Depends(get_current_user)) -> List[str]:
    """
        **Check consistency of a project**.
        
        With time and bugs, some consistency problems could be introduced in projects.
        This service aims at listing them.
    """
    with ProjectConsistencyChecker(project_id) as sce:
        with RightsThrower():
            return sce.run(current_user)


@app.get("/projects/{project_id}/stats", tags=['projects'],
responses={
             200: {
                 "content": {
                     "application/json": {
                        "example": ["Project name", "OrderedDict([('lat_end', 'n01'), ('lon_end', 'n02')])", "(0):", "Total: 0 values, dup 0 values","tot_rg20180314 (1): [43.685,43.685,#1,u1],[7.3156666667,7.3156666667,#1,u1],[9357,9357,#1,u1],[231.45,231.45,#1,u1],[10.249,10.249,#1,u1],[243,243,#1,u1],[179,179,#1,u1],[255,255,#1,u1],[171.59,171.59,#1,u1],[188.42,188.42,#1,u1],[171.2,171.2,#1,u1],[188.9,188.9,#1,u1],[3557.33,3557.33,#1,u1],[3932,3932,#1,u1],[698,698,#1,u1],[373,373,#1,u1],[350,350,#1,u1],[122.1,122.1,#1,u1],[97.6,97.6,#1,u1],[67.7,67.7,#1,u1],[0.009,0.009,#1,u1],[373.3,373.3,#1,u1],[2165655,2165655,#1,u1],[232,232,#1,u1],[-0.89,-0.89,#1,u1],[1.909,1.909,#1,u1],[4.94,4.94,#1,u1],[4196,4196,#1,u1],[698,698,#1,u1],[8895,8895,#1,u1],[1.336,1.336,#1,u1],[1766,1766,#1,u1],[1.359,1.359,#1,u1],[225,225,#1,u1],[231,231,#1,u1],[237,237,#1,u1],[0,0,#1,u1],[0,0,#1,u1],[16,16,#1,u1],[26,26,#1,u1],[0,0,#1,u1],[0,0,#1,u1],[0,0,#1,u1],[0,0,#1,u1],[0,0,#1,u1],[0,0,#1,u1],[0,0,#1,u1],[19.066,19.066,#1,u1],[19.122,19.122,#1,u1],[21,21,#1,u1],[21,21,#1,u1],[1441,1441,#1,u1],[86088,86088,#1,u1],[412.756,412.756,#1,u1],[4.556,4.556,#1,u1],[1,1,#1,u1],[109.1499080169,109.1499080169,#1,u1],[1.2448979592,1.2448979592,#1,u1],[76,76,#1,u1],[-0.4489990467,-0.4489990467,#1,u1],[1.4142135624,1.4142135624,#1,u1],[4.3205875999,4.3205875999,#1,u1],[13.1578947368,13.1578947368,#1,u1],[37.7147201027,37.7147201027,#1,u1],[3.9549031764,3.9549031764,#1,u1],[9.5361930295,9.5361930295,#1,u1],[29.1557377049,29.1557377049,#1,u1],[0.0088346243,0.0088346243,#1,u1],[0.0149948464,0.0149948464,#1,u1]","Total: 69 values, dup 69 values"]
                     }
                 }
             }
         },
         response_model=List[str])
def project_stats(project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
                  current_user: int = Depends(get_current_user)):
    """
**Returns stats** for a project.

These stats will be returned as a list containing at index :
- 0 : The **title** of the project, 
- 1 : A string containing all **freecols name and related column number**,

- 2 : **"(0):"**
- 3 :  **"Total: 0 values, dup 0 values"**

Then for each acquisition a pair of strings will be added to the list :
-  A string containing the **acquisition origin id** (the **number of objects for this acquisition**) : and then **small stats for an acquisition of a free column values inside** : [ min of values ; max of values ; distribution of the different values ; mode, i.e. freq of most frequent value]
-  A string containing the **number of total values** and the **number of duplicates values** "Total: ... values, dup ... values"

    """
    with ProjectStatsFetcher(project_id) as sce:
        with RightsThrower():
            return sce.run(current_user)


@app.post("/projects/{project_id}/recompute_geo", tags=['projects'],
          responses={
              200: {
                  "content": {
                      "application/json": {
                          "example": null
                      }
                  }
              }
          })
def project_recompute_geography(
        project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
        current_user: int = Depends(get_current_user)) -> None:
    """
        **Recompute geography information** for all samples in project.

        **Returns NULL upon success.**
        
        🔒 The user has to be *project manager*.
    """
    with ProjectsService() as sce:
        with RightsThrower():
            sce.recompute_geo(current_user, project_id)


@app.post("/file_import/{project_id}", tags=['projects'], response_model=ImportRsp)
def import_file(project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
                params: ImportReq = Body(...),
                current_user: int = Depends(get_current_user)):
    """
        **Validate or do a real import** of an EcoTaxa archive or directory.
    """
    with FileImport(project_id, params) as sce:
        with RightsThrower():
            ret = sce.run(current_user)
    return ret


@app.post("/simple_import/{project_id}", tags=['projects'], response_model=SimpleImportRsp)
def simple_import(project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
                  params: SimpleImportReq = Body(...),
                  dry_run: bool = Query(..., title="Dry run",
                                        description="If set, then only a diagnostic of doability will be done. In this case, plain value check. If no dry_run, this call will create a background job.",
                                        example=True),
                  current_user: int = Depends(get_current_user)):
    """
        **Import images only**, with same metadata for all.
    """
    with SimpleImport(project_id, params, dry_run) as sce:
        with RightsThrower():
            ret = sce.run(current_user)
    return ret


@app.delete("/projects/{project_id}", tags=['projects'],
            responses={
                200: {
                    "content": {
                        "application/json": {
                            "example": (100, 0, 10, 10)
                        }
                    }
                }
            })
def erase_project(project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
                  only_objects: bool = Query(title="Only objects",
                                             description="If set, the project structure is kept, but emptied from any object, sample, acquisition and process.",
                                             example=False, default=False),
                  current_user: int = Depends(get_current_user)) -> Tuple[int, int, int, int]:
    """
        **Delete the project.**
            
        Optionally, if "only_objects" is set, the project structure is kept,
        but emptied from any object, sample, acquisition and process.
        
        Otherwise, no trace of the project will remain in the database.

        **Returns** the number of  : **deleted objects**, 0, **deleated image rows** and **deleated image files**.
    """
    with ProjectsService() as sce:
        with RightsThrower():
            return sce.delete(current_user, project_id, only_objects)


@app.put("/projects/{project_id}", tags=['projects'],
         responses={
             200: {
                 "content": {
                     "application/json": {
                         "example": null
                     }
                 }
             }
         })
def update_project(project: ProjectModel,
                   project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
                   current_user: int = Depends(get_current_user)):
    """
        **Update the project**, return **NULL upon success.**

        Note that some fields will **NOT** be updated and simply ignored, e.g. *free_cols*.
    """
    with ProjectsService() as sce:
        with RightsThrower():
            present_project: ProjectBO = sce.query(current_user, project_id, for_managing=True, for_update=True)

        with ValidityThrower():
            # noinspection PyUnresolvedReferences
            present_project.update(session=sce.session,
                                   title=project.title, visible=project.visible, status=project.status,
                                   description=project.description,
                                   init_classif_list=project.init_classif_list,
                                   classiffieldlist=project.classiffieldlist, popoverfieldlist=project.popoverfieldlist,
                                   cnn_network_id=project.cnn_network_id, comments=project.comments,
                                   contact=project.contact,
                                   managers=project.managers, annotators=project.annotators, viewers=project.viewers,
                                   license_=project.license)

    with DBSyncService(Project, Project.projid, project_id) as ssce: ssce.wait()
    with DBSyncService(ProjectPrivilege, ProjectPrivilege.projid, project_id) as ssce: ssce.wait()


# ######################## END OF PROJECT

@app.get("/samples/search", tags=['samples'], response_model=List[SampleModel])
def samples_search(project_ids: str = Query(..., title="Project Ids",
                                            description="String containing the list of one or more project id separated by non-num char.",
                                            example="1,55"),
                   id_pattern: str = Query(..., title="Pattern Id",
                                           description="Sample id textual pattern. Use * or '' for 'any matches'. Match is case-insensitive.",
                                           example="*"),
                   current_user: Optional[int] = Depends(get_optional_current_user)) \
        -> List[SampleBO]:
    """
        **Search for samples.**
    """
    with SamplesService() as sce:
        proj_ids = _split_num_list(project_ids)
        with RightsThrower():
            ret = sce.search(current_user, proj_ids, id_pattern)
        return ret


@app.get("/sample_set/taxo_stats", tags=['samples'],
         responses={
             200: {
                 "content": {
                     "application/json": {
                         "example": {'nb_dubious': 56,
                                     'nb_predicted': 5500,
                                     'nb_unclassified': 0,
                                     'nb_validated': 1345,
                                     'projid': 1,
                                     'used_taxa': [45072, 78418, 84963, 85011, 85012, 85078]
                                     }
                     }
                 }
             }
         }, response_model=List[SampleTaxoStatsModel])  # type:ignore
def sample_set_get_stats(sample_ids: str = Query(..., title="Sample Ids",
                                                 description="String containing the list of one or more sample ids separated by non-num char.",
                                                 example="15,5"),
                         current_user: Optional[int] = Depends(get_optional_current_user)) \
        -> List[SampleTaxoStats]:
    """
        Returns **classification statistics** for the given set of samples.

        EXPECT A SLOW RESPONSE : No cache of such information anywhere.
    """
    with SamplesService() as sce:
        sample_ids = _split_num_list(sample_ids)
        with RightsThrower():
            ret = sce.read_taxo_stats(current_user, sample_ids)
        return ret


@app.post("/sample_set/update", tags=['samples'],
          responses={
              200: {
                  "content": {
                      "application/json": {
                          "example": 1
                      }
                  }
              }
          },
          response_model=int)
def update_samples(req: BulkUpdateReq = Body(...),
                   current_user: int = Depends(get_current_user)) -> int:
    """
        Do the required **update for each sample in the set.** 
        
        Any non-null field in the model is written to every impacted sample.

        **Returns the number of updated entities.**
    """
    with SamplesService() as sce:
        with RightsThrower():
            return sce.update_set(current_user, req.target_ids, req.updates)


@app.get("/sample/{sample_id}", tags=['samples'], response_model=SampleModel)
def sample_query(
        sample_id: int = Path(..., description="Internal, the unique numeric id of this sample.", example=1),
        current_user: Optional[int] = Depends(get_optional_current_user)) \
        -> SampleBO:
    """
        Returns **information about the sample** corresponding to the given id.
    """
    with SamplesService() as sce:
        with RightsThrower():
            ret = sce.query(current_user, sample_id)
        if ret is None:
            raise HTTPException(status_code=404, detail="Sample not found")
        return ret


# ######################## END OF SAMPLE

@app.get("/acquisitions/search", tags=['acquisitions'], response_model=List[AcquisitionModel])
def acquisitions_search(
        project_id: int = Query(..., title="Project id", description="The project id.", example=1),
        current_user: Optional[int] = Depends(get_optional_current_user)) \
        -> List[AcquisitionBO]:
    """
        Returns the **list of all acquisitions for a given project**.
    """
    with AcquisitionsService() as sce:
        with RightsThrower():
            ret = sce.search(current_user, project_id)
        return ret


@app.post("/acquisition_set/update",
          tags=['acquisitions'],
          responses={
              200: {
                  "content": {
                      "application/json": {
                          "example": 2
                      }
                  }
              }
          },
          response_model=int)
def update_acquisitions(req: BulkUpdateReq = Body(...),
                        current_user: int = Depends(get_current_user)) -> int:
    """
        Do the required **update for each acquisition in the set**.
        
        **Return the number of updated entities.**
    """
    with AcquisitionsService() as sce:
        with RightsThrower():
            return sce.update_set(current_user, req.target_ids, req.updates)


@app.get("/acquisition/{acquisition_id}", tags=['acquisitions'], response_model=AcquisitionModel)
def acquisition_query(
        acquisition_id: int = Path(..., description="Internal, the unique numeric id of this acquisition.",
                                   example=1),
        current_user: Optional[int] = Depends(get_optional_current_user)) \
        -> AcquisitionBO:
    """
        Returns **information about the acquisition** corresponding to the given id.
    """
    with AcquisitionsService() as sce:
        with RightsThrower():
            ret = sce.query(current_user, acquisition_id)
        if ret is None:
            raise HTTPException(status_code=404, detail="Acquisition not found")
        return ret


# ######################## END OF ACQUISITION

@app.get("/instruments/",
         tags=['instruments'],
         response_model=List[str],
         responses={
             200: {
                 "content": {
                     "application/json": {
                         "example": [
                             "uvp5",
                             "zooscan"
                         ]
                     }
                 }
             }
         }
         )
def instrument_query(project_ids: str = Query(..., title="Projects ids",
                                              description="String containing the list of one or more project id separated by non-num char.",
                                              example="1,2,3")) \
        -> List[str]:
    """
        Returns the list of instruments, inside specific project(s).
    """
    with InstrumentsService() as sce:
        proj_ids = _split_num_list(project_ids)
        with RightsThrower():
            ret = sce.query(proj_ids)
        return ret


# ######################## END OF INSTRUMENT

@app.post("/process_set/update", tags=['processes'],
          responses={
              200: {
                  "content": {
                      "application/json": {
                          "example": 1
                      }
                  }
              }
          },
          response_model=int
          )
def update_processes(req: BulkUpdateReq = Body(...),
                     current_user: int = Depends(get_current_user)) -> int:
    """
        Do the required **update for each process in the set.**

        **Returns the number of updated entities.**
    """
    with ProcessesService() as sce:
        with RightsThrower():
            return sce.update_set(current_user, req.target_ids, req.updates)


@app.get("/process/{process_id}", tags=['processes'], response_model=ProcessModel)
def process_query(
        process_id: int = Path(..., description="Internal, the unique numeric id of this process.", example=1),
        current_user: Optional[int] = Depends(get_optional_current_user)) \
        -> ProcessBO:
    """
        Returns **information about the process** corresponding to the given id.
    """
    with ProcessesService() as sce:
        with RightsThrower():
            ret = sce.query(current_user, process_id)
        if ret is None:
            raise HTTPException(status_code=404, detail="Process not found")
        return ret


# ######################## END OF PROCESS

# TODO: Should be app.get, but for this we need a way to express
#  that each field in ProjectFilter is part of the params

# TODO /query pas bon!

@app.post("/object_set/{project_id}/query", tags=['objects'], response_model=ObjectSetQueryRsp,
          response_class=MyORJSONResponse  # Force the ORJSON encoder
          )
def get_object_set(project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
                   filters: ProjectFiltersModel = Body(...),
                   fields: Optional[str] = Query(title="Fields", description='''

Specify the needed object (and ancilliary entities) fields.
                   
It follows the naming convention 'prefix.field' : Prefix is either 'obj' for main object, 'fre' for free fields, 'img' for the visible image.

The column obj.imgcount contains the total count of images for the object.

Use a comma to separate fields.                   
                   ''', default=None, example="obj.longitude,fre.feret"),
                   order_field: Optional[str] = Query(title="Order field",
                                                      description='order_field will order the result using given field. If prefixed with "-" then it will be reversed.',
                                                      default=None, example="obj.longitude"),
                   # TODO: order_field should be a user-visible field name, not nXXX, in case of free field
                   window_start: Optional[int] = Query(default=None, title="Window start",
                                                       description="Allows to return only a slice of the result. Skip window_start before returning data.",
                                                       example="10"),
                   window_size: Optional[int] = Query(default=None, title="Window size",
                                                      description="Allows to return only a slice of the result. Return only window_size lines.",
                                                      example="100"),
                   current_user: Optional[int] = Depends(get_optional_current_user)) -> ObjectSetQueryRsp:
    """
        Returns **filtred object Ids** for the given project.
    """
    return_fields = None
    if fields is not None:
        return_fields = fields.split(",")
    with ObjectManager() as sce:
        with RightsThrower():
            rsp = ObjectSetQueryRsp()
            obj_with_parents, details, total = sce.query(current_user, project_id, filters,
                                                         return_fields, order_field,
                                                         window_start, window_size)
        rsp.total_ids = total
        rsp.object_ids = [with_p[0] for with_p in obj_with_parents]
        rsp.acquisition_ids = [with_p[1] for with_p in obj_with_parents]
        rsp.sample_ids = [with_p[2] for with_p in obj_with_parents]
        rsp.project_ids = [with_p[3] for with_p in obj_with_parents]
        rsp.details = details
        # TODO: Despite the ORJSON encode above, this response is still quite slow due to many calls
        # to def jsonable_encoder (in FastAPI encoders.py)
        return rsp


@app.post("/object_set/{project_id}/summary", tags=['objects'], response_model=ObjectSetSummaryRsp)
def get_object_set_summary(project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
                           only_total: bool = Query(..., title="Only total", description="If True, returns only the **Total number of objects**. Else returns also the **Number of validated ones**, the **number of Dubious ones** and the number of **predicted ones**."),
                           filters: ProjectFiltersModel = Body(...),
                           current_user: Optional[int] = Depends(get_optional_current_user)) -> ObjectSetSummaryRsp:
    """ For the given project, with given filters, **return the classification summary**.
        
i.e.:
            
- Total number of objects

And optionnaly

- Number of Validated ones
- Number of Dubious ones
- Number of Predicted ones
    """
    with ObjectManager() as sce:
        with RightsThrower():
            rsp = ObjectSetSummaryRsp()
            rsp.total_objects, rsp.validated_objects, rsp.dubious_objects, rsp.predicted_objects \
                = sce.summary(current_user, project_id, filters, only_total)
        return rsp


@app.post("/object_set/{project_id}/reset_to_predicted", tags=['objects'], response_model=None,
responses={
              200: {
                  "content": {
                      "application/json": {
                          "example": null
                      }
                  }
              }
          })
def reset_object_set_to_predicted(project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
                                  filters: ProjectFiltersModel = Body(...),
                                  current_user: int = Depends(get_current_user)) -> None:
    """
        **Reset to Predicted** all objects for the given project with the filters.

        Return **NULL upon success.**
    """
    with ObjectManager() as sce:
        with RightsThrower():
            return sce.reset_to_predicted(current_user, project_id, filters)


@app.post("/object_set/{project_id}/revert_to_history", tags=['objects'],
          response_model=ObjectSetRevertToHistoryRsp)
def revert_object_set_to_history(project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
                                 filters: ProjectFiltersModel= Body(...),
                                 dry_run: bool = Query(..., title="Dry run",
                                                description="If set, then no real write but consequences of the revert will be replied.",
                                                example=False),                                 
                                 target: Optional[int] = Query(title="Target", description = "Use null/None for reverting using the last annotation from anyone, or a user id for the last annotation from this user.", 
                                                         default=None, example=465),
                                 current_user: int = Depends(get_current_user)) -> ObjectSetRevertToHistoryRsp:
    """
        **Revert all objects for the given project**, with the filters, to the target.
    """
    with ObjectManager() as sce:
        with RightsThrower():
            obj_hist, classif_info = sce.revert_to_history(current_user, project_id, filters, dry_run, target)
        return ObjectSetRevertToHistoryRsp(last_entries=obj_hist,
                                           classif_info=classif_info)


@app.post("/object_set/{project_id}/reclassify", tags=['objects'],
        responses={
            200: {
                "content": {
                    "application/json": {
                        "example": 298
                    }
                }
            }
        },
        response_model=int)
def reclassify_object_set(project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
                          filters: ProjectFiltersModel = Body(...),
                          forced_id: ClassifIDT = Query(..., title="Forced Id", description="The new classification Id.", example=23025),
                          reason: str = Query(..., title="Reason", description="The reason of this new classification.", example="W"),
                          current_user: int = Depends(get_current_user)) -> int:
    """
        Regardless of present classification or state, **set the new classification for this object set.**

        If the filter designates "all with given classification", add a TaxonomyChangeLog entry.

        **Returns the number of affected objects.**
    """
    with ObjectManager() as sce:
        with RightsThrower():
            nb_impacted = sce.reclassify(current_user, project_id, filters, forced_id, reason)
        return nb_impacted


@app.post("/object_set/update", tags=['objects'],
          responses={
              200: {
                  "content": {
                      "application/json": {
                          "example": 2
                      }
                  }
              }
          },
          response_model=int)
def update_object_set(req: BulkUpdateReq = Body(...),
                      current_user: int = Depends(get_current_user)) -> int:
    """
        Do the required **update for each objects in the set.** 
        
        **Returns the number of updated entities.**

        🔒 Current user needs *Manage* right on all projects of specified objects.

    """
    with ObjectManager() as sce:
        with RightsThrower():
            return sce.update_set(current_user, req.target_ids, req.updates)


@app.post("/object_set/classify", tags=['objects'],
          responses={
              200: {
                  "content": {
                      "application/json": {
                          "example": 3
                      }
                  }
              }
          },
          response_model=int)
def classify_object_set(req: ClassifyReq = Body(...),
                        current_user: int = Depends(get_current_user)) -> int:
    """
        **Change classification and/or qualification for a set of objects.**

        **Returns the number of updated entities.**

        🔒 Current user needs at *least Annotate* right on all projects of specified objects.
    """  ##**Returns the number of updated entities.**NULL upon success.
    # TODO: Cannot classify anymore to deprecated taxon/category
    assert len(req.target_ids) == len(req.classifications), "Need the same number of objects and classifications"
    with ObjectManager() as sce:
        with RightsThrower():
            ret, prj_id, changes = sce.classify_set(current_user, req.target_ids, req.classifications,
                                                    req.wanted_qualification)
        last_classif_ids = [change[2] for change in changes.keys()]  # Recently used are in first
        with UserService() as usce: usce.update_classif_mru(current_user, prj_id, last_classif_ids)
        with DBSyncService(ProjectTaxoStat, ProjectTaxoStat.projid, prj_id) as ssce: ssce.wait()
        return ret


@app.post("/object_set/classify_auto", tags=['objects'],
          responses={
              200: {
                  "content": {
                      "application/json": {
                          "example": 3                      
                        }
                  }
              }
          },
          response_model=int)
def classify_auto_object_set(req: ClassifyAutoReq = Body(...),
                             current_user: int = Depends(get_current_user)) -> int:
    """
        **Set automatic classification** of a set of objects.

        **Returns the number of updated entities.**
    """
    assert len(req.target_ids) == len(req.classifications) == len(req.scores), \
        "Need the same number of objects, classifications and scores"
    with ObjectManager() as sce:
        with RightsThrower():
            ret, prj_id, changes = sce.classify_auto_set(current_user, req.target_ids, req.classifications, req.scores,
                                                         req.keep_log)
        with DBSyncService(ProjectTaxoStat, ProjectTaxoStat.projid, prj_id) as ssce: ssce.wait()
        return ret


# TODO: For small lists we could have a GET
@app.post("/object_set/parents", tags=['objects'], response_model=ObjectSetQueryRsp,
          response_class=MyORJSONResponse  # Force the ORJSON encoder
          )
def query_object_set_parents(object_ids: ObjectIDListT = Body(..., title="Object IDs list",
                                           description="The list of object ids.",
                                           example=[634509,6234516,976544]),
                             current_user: int = Depends(get_current_user)) -> ObjectSetQueryRsp:
    """
        **Return object ids, with parent ones and projects** for the objects in given list.
    """
    with ObjectManager() as sce:
        with RightsThrower():
            rsp = ObjectSetQueryRsp()
            obj_with_parents = sce.parents_by_id(current_user, object_ids)
        rsp.object_ids = [with_p[0] for with_p in obj_with_parents]
        rsp.acquisition_ids = [with_p[1] for with_p in obj_with_parents]
        rsp.sample_ids = [with_p[2] for with_p in obj_with_parents]
        rsp.project_ids = [with_p[3] for with_p in obj_with_parents]
        rsp.total_ids = len(rsp.object_ids)
        return rsp


@app.post("/object_set/export", tags=['objects'], response_model=ExportRsp)
def export_object_set(filters: ProjectFiltersModel = Body(...),
                      request: ExportReq = Body(...),
                      current_user: Optional[int] = Depends(get_optional_current_user)) -> ExportRsp:
    """
        **Start an export job for the given object set and options.**
    """
    with ProjectExport(request, filters) as sce:
        rsp = sce.run(current_user)
    return rsp


@app.post("/object_set/predict", tags=['objects'], response_model=PredictionRsp)
def predict_object_set(filters: ProjectFiltersModel = Body(...),
                       request: PredictionReq = Body(...),
                       current_user: Optional[int] = Depends(get_optional_current_user)) -> PredictionRsp:
    """
        **Start a prediction** AKA automatic classification for the given object set and options.
    """
    with PredictForProject(request, filters) as sce:
        rsp = sce.run(current_user)
    return rsp


@app.get("/project/do_cnn", tags=['objects'],
          responses={
              200: {
                  "content": {
                      "application/json": {
                          "example": "OK, 50 CNN features computed and written"                     
                        }
                  }
              }
          }, response_model=str)
def compute_project_cnn(proj_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
                        current_user: Optional[int] = Depends(get_optional_current_user)) -> str:
    """
        **Generate CNN features** for the requested project.
        
        **Returns a string containing the number of generated features.**
    """
    with CNNForProject() as sce:
        rsp = sce.run(current_user, proj_id)
    return rsp

@app.delete("/object_set/", tags=['objects'],
            responses={
                200: {
                    "content": {
                        "application/json": {
                            "example": (100, 0, 10, 10)
                        }
                    }
                }
            })
def erase_object_set(object_ids: ObjectIDListT = Body(..., title="Object IDs list",
                                           description="The list of object ids.",
                                           example=[634509,6234516,976544]),
                     current_user: int = Depends(get_current_user)) -> Tuple[int, int, int, int]:
    """
        **Delete the objects with given object ids.** 
 
        **Returns** the number of  : **deleted objects**, 0, **deleated image rows** and **deleated image files**.
        
        🔒 Current user needs *Manage* right on all projects of specified objects.
    """
    with ObjectManager() as sce:
        with RightsThrower():
            return sce.delete(current_user, object_ids)


@app.get("/object/{object_id}", tags=['object'], response_model=ObjectModel)
def object_query(
        object_id: int = Path(..., description="Internal, the unique numeric id of this object.", example=1),
        current_user: Optional[int] = Depends(get_optional_current_user)) \
        -> ObjectBO:
    """
        Returns **information about the object** corresponding to the given id.
         
        🔒 Anonymous reader can do if the project has the right rights :)
    """
    with ObjectService() as sce:
        with RightsThrower():
            ret = sce.query(current_user, object_id)
        if ret is None:
            raise HTTPException(status_code=404, detail="Object not found")
        return ret


@app.get("/object/{object_id}/history", tags=['object'],
         responses={
             200: {
                 "content": {
                     "application/json": {
                         "example": [{
                             "objid": 264409236,
                             "classif_id": 82399,
                             "classif_date": "2021-09-21T14:59:01.007110",
                             "classif_who": "null",
                             "classif_type": "A",
                             "classif_qual": "P",
                             "classif_score": 0.085,
                             "user_name": "null",
                             "taxon_name": "Penilia avirostris"
                         }, {
                             "objid": 264409236,
                             "classif_id": 25828,
                             "classif_date": "2021-09-29T08:25:37.968095",
                             "classif_who": 1267,
                             "classif_type": "M",
                             "classif_qual": "V",
                             "classif_score": "null",
                             "user_name": "User name",
                             "taxon_name": "Copepoda"
                         }]
                     }
                 }
             }
         }, response_model=List[HistoricalClassificationModel])  # type:ignore
def object_query_history(
        object_id: int = Path(..., description="Internal, the unique numeric id of this object.", example=1),
        current_user: Optional[int] = Depends(get_optional_current_user)) \
        -> List[HistoricalClassification]:
    """
        Returns **information about the object's history** corresponding to the given id.
    """
    with ObjectService() as sce:
        with RightsThrower():
            ret = sce.query_history(current_user, object_id)
        if ret is None:
            raise HTTPException(status_code=404, detail="Object not found")
        return ret


# ######################## END OF OBJECT

@app.get("/taxa", tags=['Taxonomy Tree'], response_model=List[TaxonModel])
async def query_root_taxa() \
        -> List[TaxonBO]:
    """
        **Return all taxa with no parent.**
    """
    with TaxonomyService() as sce:
        ret = sce.query_roots()
        return ret


@app.get("/taxa/status", tags=['Taxonomy Tree'], response_model=TaxonomyTreeStatus)
async def taxa_tree_status(current_user: int = Depends(get_current_user)):
    """
        **Return the status of taxonomy tree** w/r to freshness.
    """
    with TaxonomyService() as sce:
        refresh_date = sce.status(_current_user_id=current_user)
        return TaxonomyTreeStatus(last_refresh=refresh_date.isoformat() if refresh_date else None)


@app.get("/taxa/reclassification_stats", tags=['Taxonomy Tree'], 
responses={
    200: {
            "content": {
                "application/json": {
                    "example": [{"id":12876,"renm_id":null,"name":"Echinodermata X","type":"P","nb_objects":24,"nb_children_objects":759,"display_name":"Echinodermata X","lineage":["Echinodermata X","Echinodermata","Metazoa","Holozoa","Opisthokonta","Eukaryota","living"],"id_lineage":[12876,11509,2367,382,8,2,1],"children":[16710]}]
                }
            }
        }},
response_model=List[TaxonModel])
async def reclassif_stats(taxa_ids: str= Query(..., title="Taxa ids",
                                              description="String containing the list of one or more taxa id separated by non-num char.",
                                              example="12876"),
                          current_user: Optional[int] = Depends(get_optional_current_user)) \
        -> List[TaxonBO]:
    """
        Dig into reclassification logs and, for each input category id, **determine the most chosen target category,
        excluding the advised one.**

        By convention, if nothing relevant is found, the input category itself is returned. So one can expect
        that the returned list has the same size as the required one.
    """
    with TaxonomyService() as sce:
        num_taxa_ids = _split_num_list(taxa_ids)
        with RightsThrower():
            ret = sce.most_used_non_advised(current_user, num_taxa_ids)
        return ret


@app.get("/taxa/reclassification_history/{project_id}", tags=['Taxonomy Tree'])
async def reclassif_project_stats(
        project_id: int = Path(..., description="Internal, numeric id of the project.", example=1),
        current_user: Optional[int] = Depends(get_optional_current_user)) \
        -> List[TaxonBO]:
    """
        Dig into reclassification logs and **return the associations source → target for previous reclassifications.**
    """
    with TaxonomyService() as sce:
        with RightsThrower():
            ret = sce.reclassification_history(current_user, project_id)
        return ret


@app.get("/taxon/{taxon_id}", tags=['Taxonomy Tree'],
responses={
    200: {
            "content": {
                "application/json": {
                    "example": {"id":12876,"renm_id":null,"name":"Echinodermata X","type":"P","nb_objects":24,"nb_children_objects":759,"display_name":"Echinodermata X","lineage":["Echinodermata X","Echinodermata","Metazoa","Holozoa","Opisthokonta","Eukaryota","living"],"id_lineage":[12876,11509,2367,382,8,2,1],"children":[16710]}
                }
            }
        }}, response_model=TaxonModel)
async def query_taxa(
        taxon_id: int = Path(..., description="Internal, the unique numeric id of this taxon.", example=12876),
        _current_user: Optional[int] = Depends(get_optional_current_user)) \
        -> Optional[TaxonBO]:
    """
        Returns **information about the taxon** corresponding to the given id, including its lineage.
    """
    with TaxonomyService() as sce:
        ret = sce.query(taxon_id)
        return ret


@app.get("/taxon/{taxon_id}/usage", tags=['Taxonomy Tree'], response_model=List[TaxonUsageModel])
async def query_taxa_usage(
        taxon_id: int = Path(..., description="Internal, the unique numeric id of this taxon.", example=12876),
        _current_user: Optional[int] = Depends(get_optional_current_user)) \
        -> List[TaxonUsageModel]:
    """
        **Where a given taxon is used.**
        
        Only validated uses are returned.
    """
    with TaxonomyService() as sce:
        ret = sce.query_usage(taxon_id)
        return ret


@app.get("/taxon_set/search", tags=['Taxonomy Tree'], response_model=List[TaxaSearchRsp])
async def search_taxa(query: str = Query(..., description="Use this query for matching returned taxa names.", example="Ban"),
                      project_id: Optional[int] = Query(default=None,
                                                        description="Internal, numeric id of the project.", example=1),
                      current_user: Optional[int] = Depends(get_optional_current_user)):
    """
        **Search for taxa by name.**

        Queries can be 'small', i.e. of length ﹤3 and even zero-length.

        🔓 For a public, unauthenticated call :
        - zero-length and small queries always return nothing.
        - otherwise, a full search is done and results are returned in alphabetical order.

        🔒 For an authenticated call :
        - zero-length queries: return the MRU list in full.
        - small queries: the MRU list is searched, so that taxa in the recent list are returned, if matching.
        - otherwise, a full search is done. Results are ordered so that taxa in the project list are in first,
            and are signalled as such in the response.
    """
    with TaxonomyService() as sce:
        ret = sce.search(current_user_id=current_user, prj_id=project_id, query=query)
        return ret


@app.get("/taxon_set/query", tags=['Taxonomy Tree'], response_model=List[TaxonModel])
async def query_taxa_set(ids: str = Query(..., title="Ids", description="The separator between numbers is arbitrary non-digit, e.g. ':', '|' or ','.", example="1:2:3"),
                         _current_user: Optional[int] = Depends(get_optional_current_user)) \
        -> List[TaxonBO]:
    """
        Returns **information about several taxa**, including their lineage.
    """
    with TaxonomyService() as sce:
        num_ids = _split_num_list(ids)
        ret = sce.query_set(num_ids)
        return ret


@app.get("/taxon/central/{taxon_id}", tags=['Taxonomy Tree'])
async def get_taxon_in_central(
        taxon_id: int = Path(..., description="Internal, the unique numeric id of this taxon.", example=1),
        _current_user: int = Depends(get_current_user)):
    """
        Get EcoTaxoServer full record for this taxon.
    """
    with CentralTaxonomyService() as sce:
        return sce.get_taxon_by_id(taxon_id)


# TODO JCE - examples description
# Below pragma is because we need the same params as EcoTaxoServer, but we just relay them
# noinspection PyUnusedLocal
@app.put("/taxon/central", tags=['Taxonomy Tree'])
async def add_taxon_in_central(name: str = Query(..., title="Name", description="The taxon/category verbatim name.", example="Echinodermata"),
                               parent_id: int = Query(..., title="Parent Id", description="It's not possible to create a root taxon.", example=2367),
                               taxotype: str = Query(..., title="Taxo Type", description="The taxon/category type, 'M' or 'P'.", example="P"),
                               creator_email: str = Query(..., title="Creator email", description="The email of the taxo creator.", example="user.creator@email.com"),
                               request: Request = Query(..., title="Request", description=""),
                               source_desc: Optional[str] = Query(default=None, title="Source desc", description="", example=""),
                               source_url: Optional[str] = Query(default=None, title="Source url", description="The source url.", example="http://www.google.fr/"),
                               current_user: int = Depends(get_current_user)):
    """
        **Create a taxon** on EcoTaxoServer.

        🔒 Logged user must be manager (on any project) or application admin.
    """
    with CentralTaxonomyService() as sce:
        # Clone params which are immutable
        params = {k: v for k, v in request.query_params.items()}
        return sce.add_taxon(current_user, params)


@app.get("/taxa/stats/push_to_central", tags=['Taxonomy Tree'])
async def push_taxa_stats_in_central(_current_user: int = Depends(get_current_user)):
    """
        **Push present instance stats**, into EcoTaxoServer.
    """
    with CentralTaxonomyService() as sce:
        return sce.push_stats()


@app.get("/taxa/pull_from_central", tags=['Taxonomy Tree'])
async def pull_taxa_update_from_central(_current_user: int = Depends(get_current_user)):
    """
        **Returns what changed in EcoTaxoServer managed tree** and update local tree accordingly.

        i.e. : the number of inserts as nbr_inserts, updates as nbr_updates and errors as errors.
    """
    with CentralTaxonomyService() as sce:
        return sce.pull_updates()


@app.get("/worms/{aphia_id}", tags=['Taxonomy Tree'], include_in_schema=False, response_model=TaxonModel)
async def query_taxa_in_worms(aphia_id: int,
                              # = Path(..., description="Internal, the unique numeric id of this user.", default=None)
                              _current_user: Optional[int] = Depends(get_optional_current_user)) \
        -> Optional[TaxonBO]:
    """
        Information about a single taxon in WoRMS reference, including its lineage.
    """
    with TaxonomyService() as sce:
        ret = sce.query_worms(aphia_id)
        return ret


@app.get("/taxa_ref_change/refresh", tags=['WIP'], include_in_schema=False,
         status_code=status.HTTP_200_OK)
async def refresh_taxa_db(max_requests: int,
                          current_user: int = Depends(get_current_user)) -> StreamingResponse:  # pragma:nocover
    """
        Refresh local mirror of WoRMS database.
    """
    with TaxonomyChangeService(max_requests) as sce:
        with RightsThrower():
            tsk = sce.db_refresh(current_user)
            async_bg_run(tsk)  # Run in bg while streaming logs
        # Below produces a chunked HTTP encoding, which is officially only HTTP 1.1 protocol
        return StreamingResponse(log_streamer(sce.temp_log, "Done,"), media_type="text/plain")


@app.get("/taxa_ref_change/check/{aphia_id}", tags=['WIP'], include_in_schema=False,
         status_code=status.HTTP_200_OK)
async def check_taxa_db(aphia_id: int,
                        current_user: int = Depends(get_current_user)) -> Response:  # pragma:nocover
    """
        Check that the given aphia_id is correctly stored.
    """
    with TaxonomyChangeService(1) as sce:
        with RightsThrower():
            msg = await sce.check_id(current_user, aphia_id)
        # Below produces a chunked HTTP encoding, which is officially only HTTP 1.1 protocol
        return Response(msg, media_type="text/plain")


@app.get("/taxa_ref_change/matches", tags=['WIP'], include_in_schema=False,
         status_code=status.HTTP_200_OK)
async def matching_with_worms_nice(request: Request,
                                   current_user: int = 0  # Depends(get_current_user)
                                   ) -> Response:  # pragma:nocover
    """
        Show current state of matches - HTML version.
    """
    params = request.query_params
    with TaxonomyChangeService(0) as sce:
        with RightsThrower():
            # noinspection PyProtectedMember
            data = sce.matching(current_user, params._dict)
        return templates.TemplateResponse("worms.html",
                                          {"request": request, "matches": data, "params": params},
                                          headers=CRSF_header)


# ######################## END OF TAXA_REF

@app.get("/admin/images/{project_id}/digest", tags=['WIP'], include_in_schema=False,
         response_model=str)
def digest_project_images(max_digests: Optional[int],
                          project_id: int = Path(..., description="Internal, numeric id of the project.",
                                                 example=1),
                          current_user: int = Depends(get_current_user)) -> str:
    """
        Compute digests for images referenced from a project.
    """
    max_digests = 1000 if max_digests is None else max_digests
    with ImageManagerService() as sce:
        with RightsThrower():
            data = sce.do_digests(current_user, project_id, max_digests)
        return data


@app.get("/admin/images/digest", tags=['WIP'], include_in_schema=False,
         response_model=str)
def digest_images(max_digests: Optional[int],
                  project_id: Optional[int] = Query(default=None, description="Internal, numeric id of the project."),
                  current_user: int = Depends(get_current_user)) -> str:
    """
        Compute digests if they are not.
    """
    max_digests = 1000 if max_digests is None else max_digests
    with ImageManagerService() as sce:
        with RightsThrower():
            data = sce.do_digests(current_user, prj_id=project_id, max_digests=max_digests)
        return data


@app.get("/admin/images/cleanup1", tags=['WIP'], include_in_schema=False,
         response_model=str)
def cleanup_images_1(
        project_id: int = Query(..., description="Internal, numeric id of the project.", example=1),
        max_deletes: Optional[int] = None,
        current_user: int = Depends(get_current_user)) -> str:
    """
        Remove duplicated images inside same object. Probably due to import update bug.
    """
    max_deletes = 10000 if max_deletes is None else max_deletes
    with ImageManagerService() as sce:
        with RightsThrower():
            data = sce.do_cleanup_dup_same_obj(current_user, prj_id=project_id, max_deletes=max_deletes)
        return data


@app.get("/admin/nightly", tags=['WIP'], include_in_schema=False,
         response_model=str)
def nightly_maintenance(current_user: int = Depends(get_current_user)) -> int:
    """
        Do nightly cleanups and calculations.
    """
    with NightlyJobService() as sce:
        with RightsThrower():
            data = sce.run(current_user)
        return data


@app.get("/admin/machine_learning/train", tags=['WIP'], include_in_schema=False,
         response_model=str)
def machine_learning_train(project_id: int = Query(..., title="Input project #",
                                                   description="Images will be fetched from this project.",
                                                   example="1040"),

                           model_name: str = Query(..., title="Produced model name",
                                                   description="File where the CNN model will be written.",
                                                   example="zooscan"),
                           current_user: int = Depends(get_current_user)) -> str:
    """
        Entry point for training the CNN features, from a reference project.
    """
    assert project_id is not None, "Please provide a project_id e.g. ?project_id=1234"
    assert model_name is not None, "Please provide a model name e.g. &model_name=zooscan"
    # Import here only because of numpy version conflict b/w lycon and tensorflow
    from API_operations.admin.MachineLearning import MachineLearningService

    with MachineLearningService() as sce:
        with RightsThrower():
            result = sce.train(current_user, project_id, model_name)
        return result


# ######################## END OF ADMIN

@app.get("/jobs/", tags=['jobs'], response_model=List[JobModel])
def list_jobs(for_admin: bool = Query(..., title="For admin",
                                      description="If FALSE return the jobs for current user, else return all of them.",
                                      example=False),
              current_user: int = Depends(get_current_user)) -> List[JobBO]:
    """
        **Return the jobs** for current user, or all of them if admin is asked for.
    """
    with JobCRUDService() as sce:
        with RightsThrower():
            ret = sce.list(current_user, for_admin)
    return ret


@app.get("/jobs/{job_id}/", tags=['jobs'], response_model=JobModel)
def get_job(job_id: int = Path(..., description="Internal, the unique numeric id of this job.", example=47445),
            current_user: int = Depends(get_current_user)) -> JobBO:
    """
        Returns **information about the job** corresponding to the given id.
    """
    with JobCRUDService() as sce:
        with RightsThrower():
            ret = sce.query(current_user, job_id)
        return ret


@app.post("/jobs/{job_id}/answer", tags=['jobs'],
          responses={
              200: {
                  "content": {
                      "application/json": {
                          "example": null
                      }
                  }
              }
          })
def reply_job_question(
        job_id: int = Path(..., description="Internal, the unique numeric id of this job.", example=47445),
        reply: Dict[str, Any] = Body(default={}, title="#TODO JCE Reply Model"),
        current_user: int = Depends(get_current_user)) -> None:
    """
        **Send answers to last question.** The job resumes after it receives the reply.
        
        Return **NULL upon success.**
        
        *Note: It's only about data storage here.*
        

        If the data is technically NOK e.g. not a JS object, standard 422 error should be thrown.

        If the data is incorrect from consistency point of view, the job will return in Asking state.
    """
    with JobCRUDService() as sce:
        with RightsThrower():
            sce.reply(current_user, job_id, reply)


@app.get("/jobs/{job_id}/restart", tags=['jobs'],
         responses={
             200: {
                 "content": {
                     "application/json": {
                         "example": null
                     }
                 }
             }
         })
def restart_job(
        job_id: int = Path(..., description="Internal, the unique numeric id of this job.", example=47445),
        current_user: int = Depends(get_current_user)):
    """
        **Restart the job related to the given id.**

        Return **NULL upon success.**

        🔒 The job must be in a restartable state, and be accessible to current user.
    """
    with JobCRUDService() as sce:
        with RightsThrower():
            sce.restart(current_user, job_id)


@app.get("/jobs/{job_id}/log", tags=['jobs'])
def get_job_log_file(
        job_id: int = Path(..., description="Internal, the unique numeric id of this job.", example=47445),
        current_user: int = Depends(get_current_user)) -> FileResponse:
    """
        **Return the log file produced by given job.**

        🔒 The job must be accessible to current user.
    """
    with JobCRUDService() as sce:
        with RightsThrower():
            path = sce.get_log_path(current_user, job_id)
        return FileResponse(str(path))


@app.get("/jobs/{job_id}/file", tags=['jobs'], responses={
    200: {
        "content": {"application/zip": {},
                    "text/tab-separated-values": {}},
        "description": "Return the produced file.",
    }
})
def get_job_file(
        job_id: int = Path(..., description="Internal, the unique numeric id of this job.", example=47445),
        current_user: int = Depends(get_current_user)) -> StreamingResponse:
    """
        **Return the file produced by given job.**
        
        🔒 The job must be accessible to current user.
    """
    with JobCRUDService() as sce:
        with RightsThrower():
            file_like, file_name, media_type = sce.get_file_stream(current_user, job_id)
        headers = {"content-disposition": "attachment; filename=\"" + file_name + "\""}
        return StreamingResponse(file_like, headers=headers, media_type=media_type)


@app.delete("/jobs/{job_id}", tags=['jobs'])
def erase_job(
        job_id: int = Path(..., description="Internal, the unique numeric id of this job.", example=47445),
        current_user: int = Depends(get_current_user)) -> int:
    """
        **Delete the job** from DB, with associated storage.
        
        If the job is running then kill it.

        🔒 The job must be accessible to current user.
    """
    with JobCRUDService() as sce:
        with RightsThrower():
            return sce.delete(current_user, job_id)


# ######################## END OF JOBS
#TODO JCE - description example
@app.get("/my_files/{sub_path:path}", tags=['Files'], response_model=DirectoryModel)
async def list_user_files(sub_path: str = Query(..., title="Sub path", description="", example=""),
                          current_user: int = Depends(get_current_user)) -> DirectoryModel:
    """
        **List the private files** which are usable for some file-related operations.
        
        *e.g. import.*
    """
    with UserFolderService() as sce:
        with RightsThrower():
            file_list = await sce.list(sub_path, current_user)
    return file_list


@app.post("/my_files/", tags=['Files'], response_model=str)
async def put_user_file(file: UploadFile = File(...),
                        path: Optional[str] = Form(None),
                        tag: Optional[str] = Form(None),
                        current_user: int = Depends(get_current_user)):
    """
        **Upload a file for the current user.**
        
        The returned text will contain a serve-side path which is usable for some file-related operations.
        
        *e.g. import.*
    """
    with UserFolderService() as sce:
        with RightsThrower():
            file_name = await sce.store(current_user, file, path, tag)
        return file_name

#TODO JCE - description example
@app.get("/common_files/", tags=['Files'], response_model=DirectoryModel)
async def list_common_files(path: str = Query(..., title="path", description="", example=""),
                            current_user: int = Depends(get_current_user)) -> DirectoryModel:
    """
        **List the common files** which are usable for some file-related operations.
        
        *e.g. import.*
    """
    with CommonFolderService() as sce:
        with RightsThrower():
            file_list = await sce.list(path, current_user)
    return file_list


# ######################## END OF FILES

@app.get("/status", tags=['WIP'])
def system_status(_current_user: int = Depends(get_current_user)) -> Response:
    """
        **Report the status**, mainly used for verifying that the server is up.
    """
    with StatusService() as sce:
        return Response(sce.run(), media_type="text/plain")


# ######################## END OF WIP

@app.get("/error", tags=['misc'])
def system_error(_current_user: int = Depends(get_current_user)):
    """
        **Return a 500 internal error**, on purpose so the stack trace is visible and client
        can see what it gives.
    """
    with RightsThrower():
        assert False


@app.get("/noop", tags=['misc'], response_model=Union[ObjectHeaderModel, HistoricalClassificationModel])  # type: ignore
def do_nothing(_current_user: int = Depends(get_current_user)):
    """
        **This entry point will just do nothing.**
        
        It's also used for exporting models we need on client side.
    """


@app.get("/constants", tags=['misc'], response_model=Constants)
def used_constants() -> Constants:
    """
        **Return useful strings for user dialog.**
        
        Now also used for values extracted from Config.
    """
    with ConstantsService() as sce:
        return sce.get()


# ######################## END OF MISC

# @app.get("/loadtest", tags=['WIP'], include_in_schema=False)
# def load_test() -> Response:
#     """
#         Simulate load with various response time. The Service() gets a session from the DB pool.
#         See if we just wait or fail to serve:
#         httperf --server=localhost --port=8000 --uri=/loadtest --num-conns=1000 --num-calls=10
#     """
#     with StatusService() as sce:
#     import time
#     time.sleep(random()/10)
#     return Response(sce.run(), media_type="text/plain")

app.add_exception_handler(status.HTTP_500_INTERNAL_SERVER_ERROR, internal_server_error_handler)

dump_openapi(app, __file__)


@app.on_event("startup")
def startup_event():
    JobScheduler.launch_at_interval(1)


@app.on_event("shutdown")
def shutdown_event():
    JobScheduler.shutdown()


def _split_num_list(ids):
    # Find first non-num char, decide it's a separator
    for c in ids:
        if c not in "0123456789":
            sep = c
            break
    else:
        sep = ","
    num_ids = [int(x) for x in ids.split(sep) if x.isdigit()]
    return num_ids
