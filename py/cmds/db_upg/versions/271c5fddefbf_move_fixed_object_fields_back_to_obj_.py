"""Move fixed object fields back to obj_head

Revision ID: 271c5fddefbf
Revises: da78c15a7c21
Create Date: 2021-02-03 10:04:58.437275

"""

# revision identifiers, used by Alembic.
from db_upg.versions.a74a857fe352_no_dup_fk_in_objects import (
    OBJECTS_DDL_a74a857fe352,
)  # type:ignore

revision = "271c5fddefbf"
down_revision = "da78c15a7c21"

COPY_FIXED_FIELDS = """
create temp table origs as select objfid, orig_id, object_link from obj_field ;
create unique index origs_id on origs(objfid);
update obj_head obh
   set orig_id = org.orig_id,
       object_link = org.object_link
  from origs org
 where org.objfid = obh.objid
"""

# Version below for live mode (in psql), if the above takes too long
COPY_FIXED_FIELDS_2 = """
DO $$
DECLARE
  acq_rec RECORD;
  cnt integer = 0;
  row_count integer;
BEGIN
create temp table origs as select objfid, orig_id, object_link from obj_field ;
create unique index origs_id on origs(objfid);
FOR acq_rec IN SELECT acquisid FROM acquisitions ORDER BY acquisid DESC
LOOP
    update obj_head obh
       set orig_id = org.orig_id,
           object_link = org.object_link
      from origs org
     where org.objfid = obh.objid
       and obh.objid  IN (SELECT objid FROM obj_head WHERE acquisid = acq_rec.acquisid)
       and obh.orig_id is null;
  GET DIAGNOSTICS row_count = ROW_COUNT;
  RAISE NOTICE 'Done %, % lines',acq_rec.acquisid,row_count;
  cnt = cnt + row_count; 
  IF cnt > 10000 
  THEN 
    COMMIT; RAISE NOTICE 'Commit'; cnt = 0; 
  END IF;
END LOOP;
END;
$$;
"""

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.execute("drop view objects")
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column(
        "obj_head", sa.Column("object_link", sa.VARCHAR(length=255), nullable=True)
    )
    op.add_column(
        "obj_head", sa.Column("orig_id", sa.VARCHAR(length=255), nullable=True)
    )
    op.execute(COPY_FIXED_FIELDS)
    op.alter_column("obj_head", "orig_id", nullable=False)
    op.drop_column("obj_field", "orig_id")
    op.drop_column("obj_field", "object_link")
    # ### end Alembic commands ###
    op.execute(OBJECTS_DDL_a74a857fe352)


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(None, "obj_head", type_="foreignkey")
    op.create_foreign_key(
        "obj_head_acquisid_fkey",
        "obj_head",
        "acquisitions",
        ["acquisid"],
        ["acquisid"],
        ondelete="CASCADE",
    )
    op.drop_column("obj_head", "orig_id")
    op.drop_column("obj_head", "object_link")
    op.add_column(
        "obj_field",
        sa.Column(
            "object_link", sa.VARCHAR(length=255), autoincrement=False, nullable=True
        ),
    )
    op.add_column(
        "obj_field",
        sa.Column(
            "orig_id", sa.VARCHAR(length=255), autoincrement=False, nullable=False
        ),
    )
    # ### end Alembic commands ###
