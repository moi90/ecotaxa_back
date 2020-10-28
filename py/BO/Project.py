# -*- coding: utf-8 -*-
# This file is part of Ecotaxa, see license.md in the application root directory for license informations.
# Copyright (C) 2015-2020  Picheral, Colin, Irisson (UPMC-CNRS)
#
from datetime import datetime
from typing import List, Dict, Any, Iterable, Optional

from dataclasses import dataclass

from BO.Classification import ClassifIDListT
from BO.Mappings import RemapOp, MappedTableTypeT, ProjectMapping
from BO.ProjectPrivilege import ProjectPrivilegeBO
from BO.helpers.DataclassAsDict import DataclassAsDict
from DB import ObjectHeader, Sample, ProjectPrivilege, User, Project, ObjectFields, Acquisition, Process, \
    ParticleProject, ParticleCategoryHistogramList, ParticleSample, ParticleCategoryHistogram, ObjectCNNFeature
from DB import Session, ResultProxy
from DB.User import Role
from DB.helpers.ORM import Delete, Query, select, text, any_, contains_eager
from helpers.DynamicLogs import get_logger
from helpers.Timer import CodeTimer

logger = get_logger(__name__)

# Typings, to be clear that these are not e.g. object IDs
ProjectIDT = int
ProjectIDListT = List[int]


@dataclass(init=False)
class ProjectStats(DataclassAsDict):
    """
        Association b/w an object and a former taxonomy entry.
    """
    projid: ProjectIDT
    used_taxa: ClassifIDListT
    nb_unclassified: int
    nb_validated: int
    nb_dubious: int
    nb_predicted: int


class ProjectBO(object):
    """
        A Project business object. So far (but less and less...) mainly a container
        for static API_operations involving it.
    """

    def __init__(self, project: Project):
        self._project = project
        # Added values
        self.can_administrate = False
        self.obj_free_cols: Dict[str, str] = {}
        self.sample_free_cols: Dict[str, str] = {}
        self.acquisition_free_cols: Dict[str, str] = {}
        self.process_free_cols: Dict[str, str] = {}
        self.init_classif_list: List[int] = []
        # Involved members
        self.owner: Optional[User] = None
        self.viewers: List[User] = []
        self.annotators: List[User] = []
        self.managers: List[User] = []

    def get_preset(self) -> ClassifIDListT:
        """
            Return the list of preset classification IDs.
        """
        if not self._project:
            return []
        init_list = self._project.initclassiflist
        if not init_list:
            return []
        return [int(cl_id) for cl_id in init_list.split(",")]

    def enrich(self):
        """
            Add DB fields and relations as (hopefully more) meaningful attributes
        """
        # Decode mappings to avoid exposing internal field
        mappings = ProjectMapping().load_from_project(self._project)
        self.obj_free_cols = mappings.object_mappings.tsv_cols_to_real
        self.sample_free_cols = mappings.sample_mappings.tsv_cols_to_real
        self.acquisition_free_cols = mappings.acquisition_mappings.tsv_cols_to_real
        self.process_free_cols = mappings.process_mappings.tsv_cols_to_real
        # Decode text list into numerical
        db_list = self._project.initclassiflist
        db_list = db_list if db_list else ""
        self.init_classif_list = [int(x) for x in db_list.split(",") if x.isdigit()]
        # Dispatch members by right
        by_right = {ProjectPrivilegeBO.MANAGE: self.managers,
                    ProjectPrivilegeBO.ANNOTATE: self.annotators,
                    ProjectPrivilegeBO.VIEW: self.viewers}
        a_priv: ProjectPrivilege
        # noinspection PyTypeChecker
        for a_priv in self._project.privs_for_members:
            if a_priv.user is None:  # TODO: There is a line with NULL somewhere in DB
                continue
            by_right[a_priv.privilege].append(a_priv.user)
        # Owner defaults to first historical manager
        # owner_id = self._project.owner_id
        # if owner_id == 0:
        #     if len(self.managers) > 0:
        #         self.owner = self.managers[0]
        #     else:
        #         # assert False, "No manager found for %s" % self._project.projid
        #         self.owner = MISSING_USER
        # else:
        #     self.owner = self._project.owner
        return self

    def update(self, session: Session, title: str, visible: bool, status: str, projtype: str,
               init_classif_list: List[int],
               classiffieldlist: str, popoverfieldlist: str,
               cnn_network_id: str, comments: str,
               # owner: Any,
               managers: List[Any], annotators: List[Any], viewers: List[Any],
               license: str):
        proj_id = self._project.projid
        # Field reflexes
        if cnn_network_id != self._project.cnn_network_id:
            sub_qry: Query = session.query(ObjectHeader.objid).filter(ObjectHeader.projid == proj_id)
            # Delete CNN features which depend on the CNN network
            qry: Query = session.query(ObjectCNNFeature)
            qry = qry.filter(ObjectCNNFeature.objcnnid.in_(sub_qry.subquery()))
            qry.delete(synchronize_session=False)
        # Fields update
        self._project.title = title
        self._project.visible = visible
        self._project.status = status
        self._project.projtype = projtype
        self._project.classiffieldlist = classiffieldlist
        self._project.popoverfieldlist = popoverfieldlist
        self._project.cnn_network_id = cnn_network_id
        self._project.comments = comments
        self._project.license = license
        # Inverse for extracted values
        self._project.initclassiflist = ",".join([str(cl_id) for cl_id in init_classif_list])
        # Inverse for users by privilege
        # Dispatch members by right
        by_right = {ProjectPrivilegeBO.MANAGE: managers,
                    ProjectPrivilegeBO.ANNOTATE: annotators,
                    ProjectPrivilegeBO.VIEW: viewers}
        # Remove all to avoid tricky diffs
        session.query(ProjectPrivilege). \
            filter(ProjectPrivilege.projid == proj_id).delete()
        # Add all
        for a_right, a_user_list in by_right.items():
            for a_user in a_user_list:
                session.add(ProjectPrivilege(projid=proj_id,
                                             member=a_user.id,
                                             privilege=a_right))
        # Owner update
        # assert owner.id in [mgr.id for mgr in managers], "Owner must be a Manager"
        # self._project.owner_id = owner.id
        session.commit()

    def __getattr__(self, item):
        """ Fallback for 'not found' field after the C getattr() call.
            If we did not enrich a Project field somehow then return it """
        return getattr(self._project, item)

    @staticmethod
    def update_taxo_stats(session: Session, projid: int):
        # TODO: There is a direct ref. to obj_head.projid. Problem in case of clean hierarchy.
        session.execute("""
        DELETE FROM projects_taxo_stat 
         WHERE projid = :prjid;
        INSERT INTO projects_taxo_stat(projid, id, nbr, nbr_v, nbr_d, nbr_p) 
        SELECT projid, COALESCE(classif_id, -1) id, COUNT(*) nbr, 
               COUNT(CASE WHEN classif_qual = 'V' THEN 1 END) nbr_v,
               COUNT(CASE WHEN classif_qual = 'D' THEN 1 END) nbr_d, 
               COUNT(CASE WHEN classif_qual = 'P' THEN 1 END) nbr_p
          FROM obj_head
         WHERE projid = :prjid
        GROUP BY projid, classif_id;""",
                        {'prjid': projid})

    @staticmethod
    def update_stats(session: Session, projid: int):
        session.execute("""
        UPDATE projects
           SET objcount=q.nbr, pctclassified=100.0*nbrclassified/q.nbr, pctvalidated=100.0*nbrvalidated/q.nbr
          FROM projects p
          LEFT JOIN
             (SELECT projid, SUM(nbr) nbr, SUM(CASE WHEN id>0 THEN nbr END) nbrclassified, SUM(nbr_v) nbrvalidated
                FROM projects_taxo_stat
               WHERE projid = :prjid
              GROUP BY projid) q ON p.projid = q.projid
        WHERE projects.projid = :prjid 
          AND p.projid = :prjid""",
                        {'prjid': projid})

    @staticmethod
    def read_taxo_stats(session: Session, prj_ids: ProjectIDListT) -> List[ProjectStats]:
        res: ResultProxy = \
            session.execute("""
        SELECT projid, ARRAY_AGG(id) as ids, 
               SUM(CASE WHEN id = -1 THEN nbr ELSE 0 END) as nb_u, 
               SUM(nbr_v) as nb_v, SUM(nbr_d) as nb_d, SUM(nbr_p) as nb_p
          FROM projects_taxo_stat
         WHERE projid = ANY(:ids)
      GROUP BY projid""",
                            {'ids': prj_ids})
        with CodeTimer("stats for %d projects:" % len(prj_ids), logger):
            ret = [ProjectStats(rec) for rec in res.fetchall()]
        return ret

    @staticmethod
    def projects_for_user(session: Session, user: User,
                          for_managing: bool = False,
                          also_others: bool = False,
                          title_filter: str = '',
                          instrument_filter: str = '',
                          filter_subset: bool = False) -> List[ProjectIDT]:
        """
        :param session:
        :param user: The user for which the list is needed.
        :param for_managing: If set, list the projects that the user can manage.
        :param also_others: If set, also list the projects on which given user has no right, so user can
                                request access to them.
        :param title_filter: If set, filter out the projects with title not matching the required string,
                                or if set to a number, filter out the projects of which ID does not match.
        :param instrument_filter: If set, filter out the projects which do not have given instrument in at least
                                     one sample.
        :param filter_subset: If set, filter out any project of which title contains 'subset'.
        :return: The project IDs
        """
        sql_params: Dict[str, Any] = {"user_id": user.id}

        # Default query: all projects, eventually with first manager information
        sql = """SELECT p.projid
                   FROM projects p
                   LEFT JOIN ( """ + ProjectPrivilegeBO.first_manager_by_project() + """ ) fpm 
                     ON fpm.projid = p.projid """
        if also_others:
            # Add the projects for which no entry is found in ProjectPrivilege
            sql += """
                   LEFT JOIN projectspriv pp ON p.projid = pp.projid AND pp.member = :user_id
                  WHERE pp.member is null """
        else:
            if not user.has_role(Role.APP_ADMINISTRATOR):
                # Not an admin, so restrict to projects which current user can work on, or view
                sql += """
                        JOIN projectspriv pp 
                          ON p.projid = pp.projid 
                         AND pp.member = :user_id """
                if for_managing:
                    sql += """
                         AND pp.privilege = '%s' """ % ProjectPrivilegeBO.MANAGE
            sql += " WHERE 1 = 1 "

        if title_filter != '':
            sql += """ 
                    AND ( title ILIKE '%%'|| :title ||'%%'
                          OR TO_CHAR(p.projid,'999999') LIKE '%%'|| :title ) """
            sql_params["title"] = title_filter

        if instrument_filter != '':
            sql += """
                     AND p.projid IN (SELECT DISTINCT projid FROM acquisitions 
                                       WHERE instrument ILIKE '%%'|| :instrum ||'%%' ) """
            sql_params["instrum"] = instrument_filter

        if filter_subset:
            sql += """
                     AND NOT title ILIKE '%%subset%%'  """

        with CodeTimer("Projects query:", logger):
            res: ResultProxy = session.execute(sql, sql_params)
            # single-element tuple :( DBAPI
            ret = [an_id for an_id, in res.fetchall()]
        return ret  # type:ignore

    @classmethod
    def get_bounding_geo(cls, session: Session, proj_id: int) -> Iterable[float]:
        res: ResultProxy = session.execute(
            "SELECT min(o.latitude), max(o.latitude), min(o.longitude), max(o.longitude)"
            "  FROM objects o "
            " WHERE o.projid = :prj",
            {"prj": proj_id})
        vals = res.first()
        assert vals
        return [a_val for a_val in vals]

    @classmethod
    def get_date_range(cls, session: Session, proj_id: int) -> Iterable[datetime]:
        res: ResultProxy = session.execute(
            "SELECT min(o.objdate), max(o.objdate)"
            "  FROM objects o "
            " WHERE o.projid = :prj",
            {"prj": proj_id})
        vals = res.first()
        assert vals
        return [a_val for a_val in vals]

    @staticmethod
    def do_after_load(session: Session, prj_id: int):
        """
            After loading of data, update various cross counts.
        """
        # Ensure the ORM has no shadow copy before going to plain SQL
        session.expunge_all()
        ObjectHeader.update_counts_and_img0(session, prj_id)
        Sample.propagate_geo(session, prj_id)
        ProjectBO.update_taxo_stats(session, prj_id)
        # Stats depend on taxo stats
        ProjectBO.update_stats(session, prj_id)

    @classmethod
    def delete_object_parents(cls, session: Session, prj_id: int) -> List[int]:
        """
            Remove object parents, also project children entities, in the project.
        """
        # The EcoTaxa samples which are going to disappear. We have to cleanup Particle side.
        soon_deleted_samples = Query(Sample.sampleid).filter(Sample.projid == prj_id)
        # The EcoPart samples to clean.
        soon_invalid_part_samples = Query(ParticleSample.psampleid).filter(
            ParticleSample.sampleid.in_(soon_deleted_samples))

        # Cleanup EcoPart corresponding tables
        del_qry = ParticleCategoryHistogramList.__table__. \
            delete().where(ParticleCategoryHistogramList.psampleid.in_(soon_invalid_part_samples))
        logger.info("Del part histo lst :%s", str(del_qry))
        session.execute(del_qry)
        del_qry = ParticleCategoryHistogram.__table__. \
            delete().where(ParticleCategoryHistogram.psampleid.in_(soon_invalid_part_samples))
        logger.info("Del part histo :%s", str(del_qry))
        session.execute(del_qry)
        upd_qry = ParticleSample.__table__. \
            update().where(ParticleSample.psampleid.in_(soon_invalid_part_samples)).values(sampleid=None)
        logger.info("Upd part samples :%s", str(upd_qry))
        row_count = session.execute(upd_qry).rowcount
        logger.info(" %d EcoPart samples unlinked and cleaned", row_count)

        ret = []
        # Remove first-level children of project
        for a_tbl in (Sample, Acquisition, Process):
            sub_del: Delete = a_tbl.__table__.delete().where(a_tbl.projid == prj_id)  # type: ignore
            logger.info("Del parent :%s", str(sub_del))
            row_count = session.execute(sub_del).rowcount
            ret.append(row_count)
            logger.info("%d rows deleted", row_count)
        session.commit()
        return ret

    @staticmethod
    def delete(session: Session, prj_id: int):
        """
            Completely remove the project. It is assumed that contained objects has been removed.
        """
        # TODO: Remove from user preferences
        # Unlink Particle project if any
        upd_qry = ParticleProject.__table__.update().where(ParticleProject.projid == prj_id).values(projid=None)
        row_count = session.execute(upd_qry).rowcount
        logger.info("%d EcoPart project unlinked", row_count)
        # Remove project
        session.query(Project). \
            filter(Project.projid == prj_id).delete()
        # Remove privileges
        session.query(ProjectPrivilege). \
            filter(ProjectPrivilege.projid == prj_id).delete()

    @staticmethod
    def remap(session: Session, prj_id: int, table: MappedTableTypeT, remaps: List[RemapOp]):
        """
            Apply remapping operations onto the given table for given project.
        """
        # Do the remapping, including blanking of unused columns
        values = {a_remap.to: text(a_remap.frm) if a_remap.frm is not None else a_remap.frm
                  for a_remap in remaps}
        qry: Query = session.query(table)
        if table == ObjectFields:
            # All tables have direct projid column except ObjectFields
            qry = qry.filter(ObjectFields.objfid.in_(
                select([ObjectHeader.objid]).where(ObjectHeader.projid == prj_id)))  # type: ignore
        else:
            qry = qry.filter(table.projid == prj_id)  # type: ignore
        qry = qry.update(values=values, synchronize_session=False)

        logger.info("Remap query for %s: %s", table.__tablename__, qry)

    @classmethod
    def get_all_object_ids(cls, session: Session, prj_id: int):  # TODO: Problem with recursive import -> ObjetIdListT:
        """
            Return the full list of objects IDs inside a project.
            TODO: Maybe better in ObjectBO
        """
        qry: Query = session.query(ObjectHeader.objid).filter(ObjectHeader.projid == prj_id)
        return [an_id for an_id in qry.all()]

    @classmethod
    def incremental_update_taxo_stats(cls, session: Session, prj_id: int, collated_changes: Dict):
        """
            Do not recompute the full stats for a project (which can be long).
            Instead, apply deltas because in this context we know them.
            TODO: All SQL to SQLAlchemy form
        """
        needed_ids = list(collated_changes.keys())
        # Lock taxo lines to prevent re-entering, during validation it's often a handful of them.
        pts_sql = """SELECT id
                       FROM taxonomy
                      WHERE id = ANY(:ids)
                     FOR NO KEY UPDATE
        """
        session.execute(pts_sql, {"ids": needed_ids})
        # Lock the rows we are going to update, including -1 for unclassified
        pts_sql = """SELECT id, nbr
                       FROM projects_taxo_stat 
                      WHERE projid = :prj
                        AND id = ANY(:ids)
                     FOR NO KEY UPDATE"""
        res = session.execute(pts_sql, {"prj": prj_id, "ids": needed_ids})
        ids_in_db = {classif_id: nbr for (classif_id, nbr) in res.fetchall()}
        ids_not_in_db = set(needed_ids).difference(ids_in_db.keys())
        if len(ids_not_in_db) > 0:
            # Insert rows for missing IDs
            pts_ins = """INSERT INTO projects_taxo_stat(projid, id, nbr, nbr_v, nbr_d, nbr_p) 
                         SELECT :prj, COALESCE(classif_id, -1), COUNT(*) nbr, 
                                COUNT(CASE WHEN classif_qual = 'V' THEN 1 END) nbr_v,
                                COUNT(CASE WHEN classif_qual = 'D' THEN 1 END) nbr_d,
                                COUNT(CASE WHEN classif_qual = 'P' THEN 1 END) nbr_p
                           FROM obj_head
                          WHERE projid = :prj AND COALESCE(classif_id, -1) = ANY(:ids)
                       GROUP BY classif_id"""
            session.execute(pts_ins, {'prj': prj_id, 'ids': list(ids_not_in_db)})
        # Apply delta
        for classif_id, chg in collated_changes.items():
            if classif_id in ids_not_in_db:
                # The line was created just above, with OK values
                continue
            if ids_in_db[classif_id] + chg['n'] == 0:
                # The delta means 0 for this taxon in this project, delete the line
                sqlparam = {'prj': prj_id, 'cid': classif_id}
                ts_sql = """DELETE FROM projects_taxo_stat 
                             WHERE projid = :prj AND id = :cid"""
            else:
                # General case
                sqlparam = {'prj': prj_id, 'cid': classif_id,
                            'nul': chg['n'], 'val': chg['V'], 'dub': chg['D'], 'prd': chg['P']}
                ts_sql = """UPDATE projects_taxo_stat 
                               SET nbr=nbr+:nul, nbr_v=nbr_v+:val, nbr_d=nbr_d+:dub, nbr_p=nbr_p+:prd 
                             WHERE projid = :prj AND id = :cid"""
            session.execute(ts_sql, sqlparam)


class ProjectBOSet(object):
    """
        Many projects...
    """

    def __init__(self, session: Session, prj_ids: ProjectIDListT):
        # Query the project and load neighbours as well
        qry: Query = session.query(Project, ProjectPrivilege)
        qry = qry.outerjoin(ProjectPrivilege, Project.privs_for_members).options(
            contains_eager(Project.privs_for_members))
        qry = qry.outerjoin(User, ProjectPrivilege.user).options(
            contains_eager(ProjectPrivilege.user))
        qry = qry.filter(Project.projid == any_(prj_ids))
        self.projects = []
        done = set()
        with CodeTimer("%s BO projects query & init:" % len(prj_ids), logger):
            for a_proj, a_pp in qry.all():
                # The query yields duplicates so we need to filter
                if a_proj.projid not in done:
                    self.projects.append(ProjectBO(a_proj).enrich())
                    done.add(a_proj.projid)

    def as_list(self) -> List[ProjectBO]:
        return self.projects

    @staticmethod
    def get_one(session: Session, prj_ids: ProjectIDT) -> Optional[ProjectBO]:
        """
            Get a single BO per its id
        """
        mini_set = ProjectBOSet(session, [prj_ids])
        if len(mini_set.projects) > 0:
            return mini_set.projects[0]
        else:
            return None
