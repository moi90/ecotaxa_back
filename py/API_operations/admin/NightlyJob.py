# -*- coding: utf-8 -*-
# This file is part of Ecotaxa, see license.md in the application root directory for license informations.
# Copyright (C) 2015-2020  Picheral, Colin, Irisson (UPMC-CNRS)
#
# Maintenance operations on the DB.
#
import datetime

from API_operations.helpers.JobService import JobServiceBase, ArgsDict
from BO.Job import JobBO
from BO.Project import ProjectBO
from BO.Rights import RightsBO
from BO.Taxonomy import TaxonomyBO
from DB.Job import JobIDT, Job
from DB.Project import Project, ProjectIDListT
from DB.User import Role
from FS.TempDirForTasks import TempDirForTasks
from helpers.DynamicLogs import get_logger, LogsSwitcher

logger = get_logger(__name__)


class NightlyJobService(JobServiceBase):
    """
    Mainly call relevant maintenance SQL and log output.
    """

    JOB_TYPE = "NightlyMaintenance"
    REPORT_EVERY = 20

    def __init__(self) -> None:
        super().__init__()
        self.curr = 0

    def init_args(self, args: ArgsDict) -> ArgsDict:
        """No job param"""
        return args

    def run(self, current_user_id: int) -> JobIDT:
        """
        Initial creation.
        """
        # Security check
        _user = RightsBO.user_has_role(
            self.ro_session, current_user_id, Role.APP_ADMINISTRATOR
        )
        # Security OK, create pending job
        self.create_job(self.JOB_TYPE, current_user_id)
        return self.job_id

    def do_background(self) -> None:
        """
        Background part of the job.
        """
        with LogsSwitcher(self):
            job = self._get_job()
            if job.progress_msg in (
                None,
                JobBO.PENDING_MESSAGE,
                JobBO.RESTARTING_MESSAGE,
            ):
                self.do_start()
            else:
                raise Exception("Not know progress:'%s'" % job.progress_msg)

    def do_start(self) -> None:
        logger.info("Job starting")
        self.update_progress(0, "Starting")
        all_prj_ids = [proj_id for proj_id, in self.ro_session.query(Project.projid)]
        all_prj_ids.sort()
        self.compute_all_projects_taxo_stats(all_prj_ids, 0, 30)
        self.compute_all_projects_stats(all_prj_ids, 30, 60)
        self.refresh_taxo_tree_stats()
        self.clean_old_jobs()
        self.set_job_result(errors=[], infos={"status": "ok"})
        logger.info("Job done")

    def progress_update(
        self, start: int, chunk: ProjectIDListT, total: int, end: int
    ) -> None:
        logger.info("Done for %s", chunk)
        self.curr += len(chunk)
        progress = round(start + (end - start) / total * self.curr)
        self.update_progress(progress, "Processing project %d" % chunk[-1])
        chunk.clear()

    def compute_all_projects_taxo_stats(
        self, all_proj_ids: ProjectIDListT, start: int, end: int
    ) -> None:
        """
        Update the summary projects_taxo_stat table, for all projects.
        """
        logger.info("Starting recompute of 'projects_taxo_stat' table")
        chunk = []
        total = len(all_proj_ids)
        for proj_id in all_proj_ids:
            ProjectBO.update_taxo_stats(self.session, proj_id)
            self.session.commit()
            chunk.append(proj_id)
            if len(chunk) == self.REPORT_EVERY:
                self.progress_update(start, chunk, total, end)
        logger.info("Done for %s", chunk)

    def compute_all_projects_stats(
        self, all_proj_ids: ProjectIDListT, start: int, end: int
    ) -> None:
        """
        Recompute relevant fields, directly in projects table.
        Needs @see compute_all_projects_taxo_stats first
        """
        logger.info("Starting recompute of projects' stats columns")
        chunk = []
        total = len(all_proj_ids)
        for proj_id in all_proj_ids:
            ProjectBO.update_stats(self.session, proj_id)
            self.session.commit()
            chunk.append(proj_id)
            if len(chunk) == self.REPORT_EVERY:
                self.progress_update(start, chunk, total, end)
        logger.info("Done for %s", chunk)

    def refresh_taxo_tree_stats(self) -> None:
        """
        Recompute taxonomy summaries.
        """
        logger.info("Starting recompute of taxonomy stats")
        TaxonomyBO.compute_stats(self.session)
        self.session.commit()
        logger.info("Recompute of taxonomy stats done")

    def clean_old_jobs(self) -> None:
        """
        Reclaim space on disk (and in DB) for old jobs.
        Rules: Jobs older than 30 days are erased whatever
               Jobs older than 1 week are erased if they ran OK.
        """
        logger.info("Starting cleanup of old jobs")
        thirty_days_ago = datetime.datetime.today() - datetime.timedelta(days=30)
        old_jobs_qry_1 = (
            self.ro_session.query(Job.id)
            .filter(Job.id > 0)
            .filter(Job.creation_date < thirty_days_ago)
        )
        old_jobs = [an_id for an_id, in old_jobs_qry_1]
        one_week_ago = datetime.datetime.today() - datetime.timedelta(days=7)
        old_jobs_qry_2 = (
            self.ro_session.query(Job.id)
            .filter(Job.id > 0)
            .filter(Job.creation_date < one_week_ago)
            .filter(Job.state == "F")
        )
        old_jobs_2 = [an_id for an_id, in old_jobs_qry_2]
        to_clean = set(old_jobs).union(set(old_jobs_2))
        logger.info("About to clean %d jobs %s", len(to_clean), to_clean)
        temp_for_job = TempDirForTasks(self.config.jobs_dir())
        for job_id in to_clean:
            # Commit each job, a bit inefficient but in case of trouble we have less de-sync with filesystem
            with JobBO.get_for_update(self.session, job_id) as job_bo:
                temp_for_job.archive_for(job_id, {JobServiceBase.JOB_LOG_FILE_NAME})
                job_bo.archive()
        logger.info("Cleanup of old jobs done")
