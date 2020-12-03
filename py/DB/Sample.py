# -*- coding: utf-8 -*-
# This file is part of Ecotaxa, see license.md in the application root directory for license informations.
# Copyright (C) 2015-2020  Picheral, Colin, Irisson (UPMC-CNRS)
#
from typing import List, Dict

from sqlalchemy import Index, Sequence, Column, ForeignKey
from sqlalchemy.dialects.postgresql import VARCHAR, DOUBLE_PRECISION, INTEGER

from DB.helpers.ORM import ResultProxy, Model, relationship, Session

SAMPLE_FREE_COLUMNS = 31


class Sample(Model):
    # Historical (plural) name of the table
    __tablename__ = 'samples'
    sampleid = Column(INTEGER, Sequence('seq_samples'), primary_key=True)
    projid = Column(INTEGER, ForeignKey('projects.projid'))
    # i.e. sample_id from TSV
    orig_id = Column(VARCHAR(255), nullable=False)
    latitude = Column(DOUBLE_PRECISION)
    longitude = Column(DOUBLE_PRECISION)
    dataportal_descriptor = Column(VARCHAR(8000))

    # The relationships are created in Relations.py but the typing here helps IDE
    project: relationship
    all_objects: relationship
    all_acquisitions: relationship
    ecopart_sample: relationship

    @staticmethod
    def pk_col():
        return "sampleid"

    def pk(self) -> int:
        return self.sampleid

    @classmethod
    def get_orig_id_and_model(cls, session: Session, prj_id) -> Dict[str, 'Sample']:
        res = session.query(Sample).filter(Sample.projid == prj_id)
        # sql = "SELECT orig_id, sampleid" + \
        #       "  FROM " + cls.__tablename__ + \
        #       " WHERE projid = :prj"
        # res: ResultProxy = session.execute(sql, {"prj": prj_id})
        ret = {r.orig_id: r for r in res}
        return ret

    @staticmethod
    def propagate_geo(session: Session, prj_id):
        """
            Create sample geo from objects one.
        TODO: Should be in a BO
        """
        session.execute("""
        UPDATE samples s 
           SET latitude = sll.latitude, longitude = sll.longitude
          FROM (SELECT o.sampleid, MIN(o.latitude) latitude, MIN(o.longitude) longitude
                  FROM obj_head o
                 WHERE projid = :projid 
                   AND o.latitude IS NOT NULL 
                   AND o.longitude IS NOT NULL
              GROUP BY o.sampleid) sll 
         WHERE s.sampleid = sll.sampleid 
           AND projid = :projid """,
                        {'projid': prj_id})
        session.commit()

    @classmethod
    def get_sample_summary(cls, session: Session, sample_id: int) -> List:
        res: ResultProxy = session.execute(
            "SELECT MIN(o.objdate+o.objtime), MAX(o.objdate+o.objtime), MIN(o.depth_min), MAX(o.depth_max)"
            "  FROM objects o "
            " WHERE o.sampleid = :smp",
            {"smp": sample_id})
        the_res = res.first()
        assert the_res
        return [a_val for a_val in the_res.values()]

    def __str__(self):
        return "{0} ({1})".format(self.orig_id, self.sampleid)


for i in range(1, SAMPLE_FREE_COLUMNS):
    setattr(Sample, "t%02d" % i, Column(VARCHAR(250)))

Index('IS_SamplesProjectOrigId', Sample.projid, Sample.orig_id, unique=True)
