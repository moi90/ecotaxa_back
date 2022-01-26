"""always all parents

Revision ID: 4bb7276e86de
Revises: 36bb704b9fc5
Create Date: 2020-11-24 09:17:25.672665

"""

# revision identifiers, used by Alembic.
revision = '4bb7276e86de'
down_revision = '36bb704b9fc5'

import sqlalchemy as sa
from alembic import op

cleanup_script = """
begin;
insert into samples (sampleid, orig_id, projid)
select nextval('seq_samples'), '__DUMMY_ID__'||oh.projid||'__', oh.projid
  from projects p
  join obj_head oh on oh.projid = p.projid
 where oh.sampleid is null
 group by oh.projid;
select count(1) as "NEW SAMPLES" from samples where orig_id like '__DUMMY_ID__%';
update obj_head oh
   set sampleid = (select max(sa.sampleid) from samples sa
                    where sa.projid = oh.projid
                      and sa.orig_id = '__DUMMY_ID__'||oh.projid||'__')
where oh.projid in (select projid from samples where orig_id like '__DUMMY_ID__%__')
  and oh.sampleid is null;

insert into acquisitions (acquisid, orig_id, projid)
select nextval('seq_acquisitions'), '__DUMMY_ID__'||sa.sampleid||'__', max(p.projid)
  from projects p
  join obj_head oh on oh.projid = p.projid
  join samples sa on oh.sampleid = sa.sampleid
 where oh.acquisid is null
 group by sa.sampleid;
select count(1) as "NEW ACQUISITIONS" from acquisitions where orig_id like '__DUMMY_ID__%__';
update obj_head oh
   set acquisid = (select acq.acquisid from acquisitions acq
                    where acq.projid = oh.projid
                      and acq.orig_id = '__DUMMY_ID__'||oh.sampleid||'__')
 where oh.projid in (select projid from acquisitions where orig_id like '__DUMMY_ID__%__')
   and oh.acquisid is null;

insert into process (processid, orig_id, projid)
select nextval('seq_process'), '__DUMMY_ID__'||acq.acquisid||'__', max(p.projid)
  from projects p
  join obj_head oh on oh.projid = p.projid
  join samples sa on oh.sampleid = sa.sampleid
  join acquisitions acq on oh.acquisid = acq.acquisid
 where oh.processid is null
 group by acq.acquisid;
select count(1) as "NEW PROCESSES" from process where orig_id like '__DUMMY_ID__%__';
update obj_head oh
   set processid = (select prc.processid from process prc
                     where prc.projid = oh.projid
                       and prc.orig_id = '__DUMMY_ID__'||oh.acquisid||'__')
 where oh.projid in (select projid from process where orig_id like '__DUMMY_ID__%__')
   and oh.processid is null;

commit
"""

def upgrade():
    op.execute(cleanup_script)
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('obj_head', 'acquisid', existing_type=sa.INTEGER(), nullable=False)
    op.alter_column('obj_head', 'sampleid', existing_type=sa.INTEGER(), nullable=False)
    op.alter_column('obj_head', 'processid', existing_type=sa.INTEGER(), nullable=False)
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('obj_head', 'processid', existing_type=sa.INTEGER(), nullable=True)
    op.alter_column('obj_head', 'sampleid', existing_type=sa.INTEGER(), nullable=True)
    op.alter_column('obj_head', 'acquisid', existing_type=sa.INTEGER(), nullable=True)
    # ### end Alembic commands ###
