# -*- coding: utf-8 -*-
# This file is part of Ecotaxa, see license.md in the application root directory for license informations.
# Copyright (C) 2015-2020  Picheral, Colin, Irisson (UPMC-CNRS)
#
from typing import List, Dict

from .Project import Project, ProjectIDT
from .helpers import Result
from .helpers.DDL import Index, Sequence, Column, ForeignKey
from .helpers.Direct import text
from .helpers.ORM import Model, relationship, Session
from .helpers.Postgres import VARCHAR, DOUBLE_PRECISION, INTEGER

SAMPLE_FREE_COLUMNS = 61


class Sample(Model):
    # Historical (plural) name of the table
    __tablename__ = "samples"
    sampleid: int = Column(INTEGER, Sequence("seq_samples"), primary_key=True)
    projid: int = Column(INTEGER, ForeignKey("projects.projid"), nullable=False)
    # i.e. sample_id from TSV
    orig_id: str = Column(VARCHAR(255), nullable=False)
    latitude = Column(DOUBLE_PRECISION)
    longitude = Column(DOUBLE_PRECISION)
    dataportal_descriptor = Column(VARCHAR(8000))

    # The relationships are created in Relations.py but the typing here helps IDE
    project: Project
    all_acquisitions: relationship

    def pk(self) -> int:
        return self.sampleid

    @classmethod
    def get_orig_id_and_model(
        cls, session: Session, prj_id: ProjectIDT
    ) -> Dict[str, "Sample"]:
        """
        Read in memory all Samples for given project and return them indexed by their user-visible
        unique key, AKA orig_id, in order.
        """
        res = session.query(Sample)
        res = res.join(Project)
        res = res.filter(Project.projid == prj_id)
        res = res.order_by(Sample.orig_id)
        ret = {r.orig_id: r for r in res}
        return ret

    @staticmethod
    def propagate_geo(session: Session, prj_id: ProjectIDT) -> None:
        """
            Create sample geo from objects one.
        TODO: Should be in a BO
        """
        sql = text(
            """
        UPDATE samples usam
           SET latitude = sll.latitude, longitude = sll.longitude
          FROM (SELECT sam.sampleid, MIN(obh.latitude) latitude, MIN(obh.longitude) longitude
                  FROM obj_head obh
                  JOIN acquisitions acq on acq.acquisid = obh.acquisid
                  JOIN samples sam on sam.sampleid = acq.acq_sample_id
                 WHERE sam.projid = :projid
                   AND obh.latitude IS NOT NULL
                   AND obh.longitude IS NOT NULL
              GROUP BY sam.sampleid) sll
         WHERE usam.sampleid = sll.sampleid
           AND projid = :projid """
        )
        session.execute(sql, {"projid": prj_id})
        session.commit()

    @classmethod
    def get_sample_summary(cls, session: Session, sample_id: int) -> List:
        sql = text(
            """
            SELECT MIN(obh.objdate+obh.objtime), MAX(obh.objdate+obh.objtime), MIN(obh.depth_min), MAX(obh.depth_max)
              FROM obj_head obh
              JOIN acquisitions acq on acq.acquisid = obh.acquisid
              JOIN samples sam on sam.sampleid = acq.acq_sample_id
             WHERE sam.sampleid = :smp """
        )
        res: Result = session.execute(sql, {"smp": sample_id})
        return [a_val for a_val in res.one()]

    def __str__(self):
        return "{0} ({1})".format(self.orig_id, self.sampleid)

    def __lt__(self, other):
        return self.sampleid < other.sampleid


for i in range(1, SAMPLE_FREE_COLUMNS):
    setattr(Sample, "t%02d" % i, Column(VARCHAR(250)))

Index("IS_SamplesProjectOrigId", Sample.projid, Sample.orig_id, unique=True)
