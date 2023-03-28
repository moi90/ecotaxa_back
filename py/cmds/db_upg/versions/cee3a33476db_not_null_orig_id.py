"""not null orig_id

Revision ID: cee3a33476db
Revises: 4bb7276e86de
Create Date: 2020-12-02 10:47:51.225606

"""

# revision identifiers, used by Alembic.
revision = "cee3a33476db"
down_revision = "4bb7276e86de"

import sqlalchemy as sa
from alembic import op

# There is no sample with null orig_id in production DB
# Acquisitions with null orig_id -> '__DUMMY_ID2__'||sa.sampleid||'__' (creates duplicate orig_id)
# Process with null orig_id -> '__DUMMY_ID2__'||acq.acquisid||'__' (creates duplicate orig_id)
# There is no object_field with null orig_id in production DB
cleanup_script = """
begin;

create temp table obj_paths as
select distinct projid, sampleid, acquisid, processid
  from obj_head;
create unique index obj_paths$i on obj_paths(projid, sampleid, acquisid, processid);

update acquisitions acq set orig_id = '__DUMMY_ID2__'||sam.sampleid||'__'
  from samples sam
 where acq.orig_id is null
   and exists (select 1 from obj_paths oph 
                where oph.projid = acq.projid
                  and oph.sampleid = sam.sampleid
                  and oph.acquisid = acq.acquisid);

update samples sam set orig_id = '__DUMMY_ID2__'||prj.projid||'__'
  from projects prj
 where sam.orig_id is null
   and exists (select 1 from obj_paths oph 
                where oph.projid = sam.projid
                  and oph.sampleid = sam.sampleid);

drop table obj_paths;

commit
"""


def upgrade():
    op.execute(cleanup_script)
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column(
        "acquisitions", "orig_id", existing_type=sa.VARCHAR(length=255), nullable=False
    )
    op.alter_column(
        "obj_field", "orig_id", existing_type=sa.VARCHAR(length=255), nullable=False
    )
    op.alter_column(
        "process", "orig_id", existing_type=sa.VARCHAR(length=255), nullable=False
    )
    op.alter_column(
        "samples", "orig_id", existing_type=sa.VARCHAR(length=255), nullable=False
    )
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column(
        "samples", "orig_id", existing_type=sa.VARCHAR(length=255), nullable=True
    )
    op.alter_column(
        "process", "orig_id", existing_type=sa.VARCHAR(length=255), nullable=True
    )
    op.alter_column(
        "obj_field", "orig_id", existing_type=sa.VARCHAR(length=255), nullable=True
    )
    op.alter_column(
        "acquisitions", "orig_id", existing_type=sa.VARCHAR(length=255), nullable=True
    )
    # ### end Alembic commands ###
