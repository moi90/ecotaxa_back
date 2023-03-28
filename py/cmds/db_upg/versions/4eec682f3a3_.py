"""empty message

Revision ID: 4eec682f3a3
Revises: 17b02b46329
Create Date: 2015-06-15 22:01:01.392310

"""

# revision identifiers, used by Alembic.
revision = "4eec682f3a3"
down_revision = "17b02b46329"

import sqlalchemy as sa
from alembic import op


def upgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.add_column("projects", sa.Column("comments", sa.VARCHAR(), nullable=True))
    op.add_column(
        "projects", sa.Column("projtype", sa.VARCHAR(length=50), nullable=True)
    )
    ### end Alembic commands ###


def downgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.drop_column("projects", "projtype")
    op.drop_column("projects", "comments")
    ### end Alembic commands ###
