# -*- coding: utf-8 -*-
# This file is part of Ecotaxa, see license.md in the application root directory for license informations.
# Copyright (C) 2015-2020  Picheral, Colin, Irisson (UPMC-CNRS)
#
#
# An Object as seen by the user, i.e. the fields regardless of their storage.
# An Object cannot exist outside of a project due to "free" columns.
#
from typing import Tuple, List, Optional, Any, ClassVar

from sqlalchemy import MetaData

from BO.Acquisition import AcquisitionIDT
from BO.Classification import HistoricalClassificationListT, HistoricalClassification
from BO.Mappings import TableMapping
from BO.Sample import SampleIDT
from BO.helpers.MappedEntity import MappedEntity
from DB.Acquisition import Acquisition
from DB.Image import Image
from DB.Object import ObjectHeader, ObjectFields, ObjectsClassifHisto, ObjectIDT
from DB.Project import ProjectIDT, Project
from DB.Sample import Sample
from DB.Taxonomy import Taxonomy
from DB.User import User
from DB.helpers.ORM import Session, joinedload, subqueryload, Model, minimal_model_of
from helpers.DynamicLogs import get_logger

# Typings, to be clear that these are not e.g. project IDs
ObjectIDWithParentsT = Tuple[ObjectIDT, AcquisitionIDT, SampleIDT, ProjectIDT]

logger = get_logger(__name__)


def _get_proj(obj: ObjectHeader) -> Project:
    return obj.acquisition.sample.project


class ObjectBO(MappedEntity):
    """
    An object, as seen from user. No storage/DB-related distinction here.
    """

    FREE_COLUMNS_ATTRIBUTE: ClassVar = "fields"
    PROJECT_ACCESSOR: ClassVar = _get_proj
    MAPPING_IN_PROJECT: ClassVar = "object_mappings"

    def __init__(
            self,
            session: Session,
            object_id: ObjectIDT,
            db_object: Optional[ObjectHeader] = None,
            db_fields: Optional[Model] = None,
    ):
        super().__init__(session)
        # Below is needed because validity test reads the attribute
        self.fields: Optional[ObjectFields] = None
        self.header: ObjectHeader
        if db_object is None:
            # Initialize from the unique ID
            qry = self._session.query(ObjectHeader)
            qry = qry.filter(ObjectHeader.objid == object_id)
            qry = qry.options(joinedload(ObjectHeader.fields))
            qry = qry.options(subqueryload(ObjectHeader.all_images))
            self.header = qry.scalar()
            if self.header is None:
                return
            self.fields = self.header.fields
        else:
            # Initialize from provided models
            self.header = db_object
            self.fields = db_fields  # type:ignore
        self.sample_id = self.header.acquisition.acq_sample_id
        self.project_id = self.header.acquisition.sample.projid
        # noinspection PyTypeChecker
        self.images: List[Image] = [an_img for an_img in self.header.all_images]

    def get_history(self) -> HistoricalClassificationListT:
        """
        Return classification history, user-displayable with names lookup but keeping IDs.
        """
        och = ObjectsClassifHisto
        qry = self._session.query(
            och.objid,
            och.classif_id,
            och.classif_date,
            och.classif_who,
            och.classif_type,
            och.classif_qual,
            och.classif_score,
            User.name.label("user_name"),
            Taxonomy.display_name.label("taxon_name"),
        ).filter(ObjectsClassifHisto.objid == self.header.objid)
        qry = qry.outerjoin(User)
        qry = qry.outerjoin(Taxonomy, Taxonomy.id == och.classif_id)
        ret = [HistoricalClassification(**rec._mapping) for rec in qry]
        return ret

    @staticmethod
    def _field_to_db_col(a_field: str, mappings: TableMapping) -> Optional[str]:
        try:
            prfx, name = a_field.split(".", 1)
        except ValueError:
            return None
        if prfx == "obj":
            if name in ObjectHeader.__dict__:
                return "obh." + name
            elif name == "imgcount":
                return "(SELECT COUNT(img2.imgrank) FROM images img2 WHERE img2.objid = obh.objid) AS imgcount"
        elif prfx == "fre":
            if name in mappings.tsv_cols_to_real:
                return "obf." + mappings.tsv_cols_to_real[name]
        elif prfx == "img":
            if name in Image.__dict__:
                return a_field
        elif prfx in ("txo", "txp"):
            if name in Taxonomy.__dict__:
                return a_field
        elif prfx == "sam":
            if name in Sample.__dict__:
                return a_field
        elif prfx == "acq":
            if name in Acquisition.__dict__:
                return a_field
        elif prfx == "usr":
            if name in User.__dict__:
                return a_field
        return None

    @classmethod
    def resolve_fields(
            cls, fields_list: Optional[List[str]], mappings: TableMapping
    ) -> List[str]:
        if fields_list is None or len(fields_list) == 0:
            return []
        ret = []
        for a_field in fields_list:
            a_col = ObjectBO._field_to_db_col(a_field, mappings)
            if a_col is None:
                logger.warning("Dropped unknown %s during resolve", a_field)
                continue
            ret.append(a_col)
        return ret

    def __getattr__(self, item):
        """Fallback for 'not found' field after the C getattr() call.
        If we did not enrich/modify an Object field somehow then return it"""
        try:
            return getattr(self.header, item)
        except AttributeError:
            return getattr(self.fields, item)


class ObjectBOSet(object):
    """
    Lots of ObjectBOs, because working one by one is slow...
    Also cook a view on the fields in use
    TODO: Apply calculations onto set.
    """

    def __init__(self, session: Session, object_ids: Any, obj_mapping: TableMapping):
        needed_cols = obj_mapping.real_cols_to_tsv.keys()
        # noinspection PyPep8Naming
        ReducedObjectFields = minimal_model_of(
            MetaData(), ObjectFields, set(needed_cols)
        )
        qry = session.query(ObjectHeader, ReducedObjectFields)
        qry = qry.filter(ObjectHeader.objid.in_(object_ids))
        # noinspection PyUnresolvedReferences
        qry = qry.join(
            ReducedObjectFields, ObjectHeader.objid == ReducedObjectFields.objfid  # type:ignore
        )
        qry = qry.options(joinedload(ObjectHeader.all_images))
        self.all = [
            ObjectBO(session, 0, an_obj, its_fields) for an_obj, its_fields in qry
        ]
