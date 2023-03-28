"""Job table & collection permalink

Revision ID: dae002b5d15a
Revises: 21bb404620d5
Create Date: 2021-05-05 16:35:48.159390

"""

# revision identifiers, used by Alembic.
revision = "dae002b5d15a"
down_revision = "21bb404620d5"

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "job",
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("owner_id", sa.INTEGER(), nullable=False),
        sa.Column("type", sa.VARCHAR(length=80), nullable=False),
        sa.Column("params", sa.VARCHAR(), nullable=True),
        sa.Column("state", sa.VARCHAR(length=1), nullable=True),
        sa.Column("step", sa.INTEGER(), nullable=True),
        sa.Column("progress_pct", sa.INTEGER(), nullable=True),
        sa.Column("progress_msg", sa.VARCHAR(), nullable=True),
        sa.Column("messages", sa.VARCHAR(), nullable=True),
        sa.Column("inside", sa.VARCHAR(), nullable=True),
        sa.Column("question", sa.VARCHAR(), nullable=True),
        sa.Column("reply", sa.VARCHAR(), nullable=True),
        sa.Column("result", sa.VARCHAR(), nullable=True),
        sa.Column("creation_date", postgresql.TIMESTAMP(), nullable=False),
        sa.Column("updated_on", postgresql.TIMESTAMP(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column(
        "collection", sa.Column("short_title", sa.VARCHAR(length=64), nullable=True)
    )
    op.alter_column(
        "collection", "external_id", existing_type=sa.VARCHAR(), nullable=False
    )
    op.alter_column(
        "collection", "external_id_system", existing_type=sa.VARCHAR(), nullable=False
    )
    op.create_index("CollectionShortTitle", "collection", ["short_title"], unique=True)
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index("CollectionShortTitle", table_name="collection")
    op.alter_column(
        "collection", "external_id_system", existing_type=sa.VARCHAR(), nullable=True
    )
    op.alter_column(
        "collection", "external_id", existing_type=sa.VARCHAR(), nullable=True
    )
    op.drop_column("collection", "short_title")
    op.drop_table("job")
    # ### end Alembic commands ###
